#!/usr/bin/env python3
# doctrine: scripted-release-pipeline
"""github-burst-guard.py — refuse Bash commands that burst the shared GitHub account.

WHY THIS EXISTS. On 2026-10-06 one session ran a shell loop that made about 55
`gh` calls within seconds, then ran `ops/release-pipeline.py tick` by hand three
times in five minutes. GitHub's short-window (secondary) rate limit answered
403 for the whole account, which every session, CI watcher, review loop and the
release pipeline share, and an urgent production release stopped behind the
lockout. Joe ordered a rule (74ddb23c, "never burst GitHub") and enforcement;
this hook is the enforcement.

WHAT IT REFUSES (PreToolUse, Bash and the Codex exec shapes):
  1. A shell loop — for / while / until, or xargs / parallel — that runs `gh`
     with no `sleep N` (N >= 2) inside it. Allowed: the same loop with that
     sleep, which covers the `until gh ...; do sleep 30; done` poll; and a
     `for` over a short literal list (items x gh calls per pass <= 10). Ten
     calls cannot trip the short-window limit, and checking a handful of
     named PRs this way is routine: replaying recorded Bash commands showed
     most loop refusals under a three-item cap were exactly that.
  2. A release-pipeline tick (ops/release-pipeline*.py tick, any path or copy)
     or out/orch/shepherd.sh started within five minutes of the last one this
     hook saw start. One shared window: shepherd ticks the pipeline itself.
  3. During a fifteen-minute cooldown after a recorded GitHub rate-limit answer:
     every gh call and the release tick, except `gh api rate_limit`.

WHAT IT RECORDS (PostToolUse / PostToolUseFailure, same file): when a command
that touches GitHub (gh, the release tick, shepherd, api.github.com) prints
"API rate limit exceeded" or "secondary rate limit", the time goes to the state
file and starts the cooldown. Output of any other command is ignored, so
reading this file or its selftest cannot lock the account's sessions out.

ALWAYS ALLOWED outside the cooldown, when not inside a loop: a single gh
command, gh --paginate, and gh api graphql. One paginated call is the cheap
alternative the refusals name. Text the shell will not run (a heredoc body, a
commit message, a --body or grep pattern in quotes) is not scanned for gh.

STATE: out/github-burst-guard-state.json (out/ is git-ignored), shared by every
session on the machine because the limit is per account, not per session.
CARR_GITHUB_BURST_STATE overrides the path for the selftest.

KNOWN LIMIT. It sees the command a session issues, not what a script does
inside itself, and a loop hidden behind a variable or a script file is not
visible. The threat model is the 2026-10-06 shape: a session typing the burst.

FAILS OPEN. An internal error, malformed input or an unreadable state file
allows the call and logs the reason to out/hook-guard.log.

Fixtures: ops/github-burst-guard-selftest.py
"""
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.environ.get("CARR_HOOK_GUARD_LOG") or os.path.join(REPO, "out", "hook-guard.log")
STATE = os.environ.get("CARR_GITHUB_BURST_STATE") or os.path.join(
    REPO, "out", "github-burst-guard-state.json")

MIN_LOOP_SLEEP = 2
POLL_SLEEP_HINT = 30                    # named in refusals; any sleep >= 2 passes
MAX_LITERAL_CALLS = 10                  # items x gh calls per pass, literal lists only
TICK_WINDOW = 5 * 60
COOLDOWN = 15 * 60

# A gh word: `gh` on its own followed by a subcommand. `gh-pages`, `ghost`
# and paths ending in /gh-something do not match. Only counted where it is
# RUN: at a command position (GH_CMD_RE), or as the program an xargs /
# parallel fans out to. So `grep "gh pr merge" f` is not a gh call.
GH_RE = re.compile(r"(?<![\w./-])gh\s+(?=[a-z])")
SLEEP_RE = re.compile(r"(?<![\w.-])sleep\s+(\d+(?:\.\d+)?)([smhd]?)\b")
RATE_LIMIT_TEXT = re.compile(r"api rate limit exceeded|secondary rate limit", re.I)

# Where a command word can start: string or line start, after a shell
# separator (an escaped `\|` inside a grep pattern is not one), inside a
# subshell or substitution, after a shell keyword, or at the start of a quoted
# `sh -c` body.
_CMD_START = (r"(?:^|(?<=[;&(\n`{!])|(?<=(?<!\\)\|)|(?<=-c\s['\"])|(?<=\bthen\s)"
              r"|(?<=\bdo\s)|(?<=\belse\s)|(?<=\bif\s)|(?<=\belif\s)|(?<=\bwhile\s)"
              r"|(?<=\buntil\s))\s*")
_PREFIX = (r"(?:(?:env|nohup|exec|time|command|timeout\s+\S+|caffeinate(?:\s+-\w+)*)\s+)*"
           r"(?:[A-Za-z_]\w*=\S*\s+)*")
GH_CMD_RE = re.compile(_CMD_START + _PREFIX + r"(gh)\s+(?=[a-z])", re.M)
RATE_PROBE_RE = re.compile(r"gh\s+api\s+/?rate_limit\b")
# `zsh -n script` only parses it, so -n does not count as running it.
_INTERP = r"(?:(?:\S*/)?(?:python3?(?:\.\d+)?|bash|sh|zsh)\s+(?:-(?!n\b)\S+\s+)*|uv\s+run\s+)?"
TICK_RE = re.compile(_CMD_START + _PREFIX + _INTERP +
                     r"\S*release-pipeline[\w.-]*\.py\s+(?:-\S+\s+)*tick\b", re.M)
SHEPHERD_RE = re.compile(_CMD_START + _PREFIX + _INTERP + r"(?:\S*/)?shepherd\.sh\b", re.M)
LOOP_KW_RE = re.compile(_CMD_START + r"(for|while|until)\b", re.M)
FANOUT_RE = re.compile(_CMD_START + _PREFIX + r"(xargs|parallel)\b", re.M)
WORD_RE = re.compile(r"(?<![\w$-])(do|done)(?![\w-])")
GITHUB_HOST_RE = re.compile(r"api\.github\.com")

# Shown with every refusal: the cheaper alternatives, named once.
CHEAPER = ("One `gh api --paginate` or `gh api graphql` call returns what a loop "
           "of per-item calls does; or put `sleep 2` (or more) inside the loop.")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} github-burst-guard {msg.rstrip()}\n")
    except OSError:
        pass


def inert_stripped(cmd):
    """Heredoc bodies and quoted -m messages are data, not commands."""
    try:
        from cmd_text import strip_inert_text
        return strip_inert_text(cmd)
    except Exception:
        return cmd


def mask_quoted_data(cmd):
    """Blank the inside of quoted strings the shell will not run.

    A `--body "..."`, a grep pattern or a sed script that merely mentions
    `gh pr view` or `release-pipeline.py tick` is data. Kept as code: an
    `sh -c` / `bash -c` body, and a double-quoted string holding `$` or a
    backtick, whose substitutions (`"$(gh pr view $n)"`) do run. Offsets are
    preserved, so every later match still lines up with the original text.
    """
    out = list(cmd)
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c not in "'\"":
            i += 1
            continue
        j = i + 1
        while j < len(cmd) and cmd[j] != c:
            j += 2 if (c == '"' and cmd[j] == "\\") else 1
        inner = cmd[i + 1:j]
        runs = re.search(r"-c\s*$", cmd[max(0, i - 4):i]) is not None
        if not runs and not (c == '"' and re.search(r"[$`]", inner)):
            for k in range(i + 1, min(j, len(cmd))):
                if out[k] != "\n":
                    out[k] = " "
        i = j + 1
    return "".join(out)


# ---- state ----------------------------------------------------------------
class StateUnreadable(Exception):
    pass


def read_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise StateUnreadable(str(exc))
    if not isinstance(data, dict):
        raise StateUnreadable("state is not an object")
    return data


def write_state(**updates):
    try:
        state = read_state()
    except StateUnreadable:
        state = {}
    state.update(updates)
    tmp = f"{STATE}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE)
    except OSError as exc:
        log(f"STATE-WRITE-FAILED {exc}")


def stamp(state, key):
    value = state.get(key)
    return value if isinstance(value, (int, float)) else None


# ---- command analysis -----------------------------------------------------
def sleeps(text):
    """Every `sleep N` in text, in seconds."""
    scale = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
    return [float(n) * scale[unit] for n, unit in SLEEP_RE.findall(text)]


def loop_regions(cmd):
    """(keyword, header, body) for each for/while/until ... do ... done."""
    out = []
    for m in LOOP_KW_RE.finditer(cmd):
        start = m.end()
        tokens = list(WORD_RE.finditer(cmd, start))
        if not tokens or tokens[0].group(1) != "do":
            continue
        depth = 0
        end = None
        for t in tokens:
            depth += 1 if t.group(1) == "do" else -1
            if depth == 0:
                end = t
                break
        if end is None:
            continue
        out.append((m.group(1), cmd[start:tokens[0].start()], cmd[tokens[0].end():end.start()]))
    return out


def statement_end(cmd, pos):
    """End of the shell statement starting at pos: the next unquoted ; | & or newline."""
    quote = None
    i = pos
    while i < len(cmd):
        c = cmd[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "\\":
            i += 2
            continue
        elif c in ";|&\n":
            return i
        i += 1
    return len(cmd)


def literal_item_count(header):
    """Item count of `for x in a b c`, or None when the list is not literal."""
    m = re.match(r"\s*\w+\s+in\s+(.*?)\s*;?\s*$", header, re.S)
    if not m:
        return None
    items = m.group(1)
    if re.search(r"[$`*?\[{]", items):
        return None
    try:
        return len(shlex.split(items))
    except ValueError:
        return None


def fanout_segments(cmd):
    """(program, text) for each xargs / parallel and the statement it runs."""
    for m in FANOUT_RE.finditer(cmd):
        yield m.group(1), cmd[m.end():statement_end(cmd, m.end())], m.end()


def gh_calls(text):
    """Offsets where each gh the shell would RUN in text begins."""
    found = {m.start(1) for m in GH_CMD_RE.finditer(text)}
    for _, segment, offset in fanout_segments(text):
        found.update(offset + m.start() for m in GH_RE.finditer(segment))
    return found


def loop_reason(cmd):
    for keyword, header, body in loop_regions(cmd):
        gh_header = len(gh_calls(header))
        gh_body = len(gh_calls(body))
        if gh_header + gh_body == 0:
            continue
        # The `until gh ...; do sleep 30; done` poll needs no branch of its
        # own: its sleep already clears MIN_LOOP_SLEEP. The selftest pins it.
        if any(s >= MIN_LOOP_SLEEP for s in sleeps(header + body)):
            continue
        if keyword == "for" and gh_body and not gh_header:
            n = literal_item_count(header)
            if n is not None and n * gh_body <= MAX_LITERAL_CALLS:
                continue
        return f"a `{keyword}` loop that runs gh on every pass with no `sleep {MIN_LOOP_SLEEP}`+ inside it"
    for program, segment, _ in fanout_segments(cmd):
        if GH_RE.search(segment) and not any(s >= MIN_LOOP_SLEEP for s in sleeps(segment)):
            return f"`{program}` fanning gh out over a list with no `sleep {MIN_LOOP_SLEEP}`+ between calls"
    return None


def runs_release_tick(cmd):
    return bool(TICK_RE.search(cmd) or SHEPHERD_RE.search(cmd))


def gh_calls_only_rate_probe(cmd):
    """True when every gh the command runs is `gh api rate_limit`."""
    return all(RATE_PROBE_RE.match(cmd, pos) for pos in gh_calls(cmd))


def touches_github(cmd):
    return bool(gh_calls(cmd) or runs_release_tick(cmd) or GITHUB_HOST_RE.search(cmd))


# ---- the two halves -------------------------------------------------------
def pre_verdict(cmd, now):
    """Return a refusal reason, or None to allow. Records an allowed tick."""
    scan = mask_quoted_data(inert_stripped(cmd))
    has_gh = bool(gh_calls(scan))
    tick = runs_release_tick(scan)
    if not has_gh and not tick:
        return None
    try:
        state = read_state()
    except StateUnreadable as exc:
        log(f"ALLOW(state-unreadable) {exc}")
        return None

    limited = stamp(state, "rate_limited_at")
    if limited is not None and now - limited < COOLDOWN and not (has_gh and not tick and gh_calls_only_rate_probe(scan)):
        left = int((COOLDOWN - (now - limited)) // 60) + 1
        return (f"GITHUB BURST GUARD: GitHub answered with a rate-limit error "
                f"{int((now - limited) // 60)} minute(s) ago, and every session, CI "
                f"watcher and the release pipeline share that account. Any more calls "
                f"now extend the lockout. Wait about {left} more minute(s). "
                f"`gh api rate_limit` is still allowed and shows when the limit resets "
                f"(rule 74ddb23c, never burst GitHub).")

    if has_gh:
        why = loop_reason(scan)
        if why:
            return (f"GITHUB BURST GUARD: this command is {why}. A burst like that "
                    f"trips GitHub's short-window rate limit for the whole shared "
                    f"account; on 2026-10-06 one such loop (~55 calls in seconds) "
                    f"locked out an urgent production release. {CHEAPER} A poll "
                    f"of the form `until gh ...; do sleep {POLL_SLEEP_HINT}; done` "
                    f"is also allowed (rule 74ddb23c, never burst GitHub).")

    if tick:
        last = stamp(state, "release_tick_at")
        if last is not None and now - last < TICK_WINDOW:
            wait = int(TICK_WINDOW - (now - last)) + 1
            return (f"GITHUB BURST GUARD: a release-pipeline tick or shepherd run "
                    f"started {int(now - last)}s ago. Each tick makes many GitHub calls, "
                    f"and hand-run ticks stacked on the scheduled ones are what tripped "
                    f"the rate limit on 2026-10-06. Wait {wait}s, or read the pipeline's "
                    f"state instead of ticking it again; one `gh pr view <n> --json "
                    f"state,mergedAt` answers most status questions (rule 74ddb23c, "
                    f"never burst GitHub).")
        write_state(release_tick_at=now)
    return None


def response_text(payload):
    parts = []
    for key in ("tool_response", "tool_output", "toolResponse", "error"):
        value = payload.get(key)
        if value is None:
            continue
        parts.append(value if isinstance(value, str) else json.dumps(value))
    return "\n".join(parts)


def observe(payload, cmd, now):
    if not touches_github(mask_quoted_data(inert_stripped(cmd))):
        return
    if RATE_LIMIT_TEXT.search(response_text(payload)):
        write_state(rate_limited_at=now)
        log(f"RECORDED rate-limit answer :: {cmd[:200]}")


def command_of(payload):
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if tool not in ("Bash", "exec_command", "functions.exec"):
        return None
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    cmd = (ti.get("command") or ti.get("cmd") or "") if isinstance(ti, dict) else ti
    return cmd if isinstance(cmd, str) and cmd else None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) {type(exc).__name__}")
        return 0
    try:
        if not isinstance(payload, dict):
            return 0
        cmd = command_of(payload)
        if cmd is None:
            return 0
        event = payload.get("hook_event_name") or "PreToolUse"
        now = time.time()
        if event in ("PostToolUse", "PostToolUseFailure"):
            observe(payload, cmd, now)
            return 0
        reason = pre_verdict(cmd, now)
        if reason:
            log(f"DENY {reason[:120]} :: {cmd[:300]}")
            # Exit 2 + stderr, as guard-unattended.py: it blocks on every build.
            print(reason, file=sys.stderr)
            return 2
        return 0
    except Exception as exc:                       # fail OPEN
        log(f"ALLOW(internal-error) {type(exc).__name__}: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
