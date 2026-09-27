"""rule_boot_gate.py — state and classification for the gated rule boot.

WHAT THE GATE IS. Joe asked for 100% recall on relevant rules. Nothing put the
rules in front of a context before it acted, so the design Jev chose (p=1.00)
is a gate: every session and every subagent must fetch every page of
standing-context `detail: "boot"` (mcp-server/src/rule-boot.js: the full text
of the always-on rules plus a one-line index of every active rule) before any
other tool call runs. hooks/rule-boot-gate.py enforces it on PreToolUse;
hooks/gate-integrity.py arms (and re-arms) it at SessionStart. Both import
this module so the state layout and the fetch-call grammar live in one place.

DATABASE AND CODE ONLY. The state here holds the digest, the page count and
which pages a context has fetched. It never holds rule text.

STATE LAYOUT (under out/rule-boot-gate/, gitignored, per machine):
    <session>/arm.json                      what SessionStart armed: status,
                                            digest, pages_total, epoch
    <session>/fetched/<agent>/<key>/p<N>    one empty marker per fetched page
Marker files, not a read-modify-write JSON, because a model often fetches the
pages in parallel and parallel hooks would otherwise lose each other's pages.
<key> is the digest for a subagent and digest+epoch for the main context, so
a SessionStart re-arm (startup, resume, clear, compact) makes the main context
fetch again while a digest change re-arms every context of the session.

NEVER A DEADLOCK. The fetch call itself is always allowed and is recorded when
it is ATTEMPTED (PreToolUse cannot see the result), so a context whose fetch
fails still gets through, and the store-unreachable arm status allows every
call with a loud RULES UNAVAILABLE notice (CLAUDE.md: no local fallback, say
so). The honest cost of recording at attempt time: a fetch that errors counts
as fetched; the model sees the error in the tool result itself.

A LIBRARY: no shebang, no main guard (see ops/jev_judge.py's docstring on the
SCAC inventory).
"""
import json
import os
import re
import secrets
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = "carr-rule-boot-gate/v1"

# Read-only rule verbs a context may call before its boot is complete. They
# read rules and doctrine and write nothing.
READ_ONLY_RULE_VERBS = frozenset({
    "standing-context", "applicable-rules", "resolve-doctrine-rules",
    "read-doctrine", "search-doctrine", "doctrine-index", "doctrine-sections",
})
# Tools that are needed to REACH the fetch: in Claude Code, MCP tool schemas
# are deferred and must be loaded with ToolSearch before they can be called.
REACH_TOOLS = frozenset({"ToolSearch"})

_MCP_VERB = re.compile(r"^mcp__.+__([a-z][a-z0-9-]*)$")
# The ONE shell form of the fetch: `<run.sh> call standing-context '<json>'`,
# nothing before or after it. The JSON sits in single quotes (no expansion)
# and may not itself contain a quote. <run.sh> is ./run.sh (checked against
# the session cwd below), this checkout's absolute run.sh, or ~/carr-system.
_BASH_FETCH = re.compile(
    r"^(?P<prog>\./run\.sh|~/carr-system/run\.sh|/[^\s'\"`$;&|<>()\\]+/run\.sh)"
    r"\s+call\s+standing-context(?:\s+'(?P<json>[^']*)')?\s*$")

UNAVAILABLE_NOTICE = "RULES UNAVAILABLE — store unreachable; say so before acting."


def state_root():
    # Test and replay override only; a session cannot set a hook's environment.
    return os.environ.get("CARR_RULE_BOOT_STATE_DIR") or os.path.join(REPO, "out", "rule-boot-gate")


def safe_key(value, default):
    text = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or ""))[:128].strip("._")
    return text or default


def _session_dir(session_id):
    return os.path.join(state_root(), safe_key(session_id, "no-session"))


def read_arm(session_id):
    try:
        with open(os.path.join(_session_dir(session_id), "arm.json"), encoding="utf-8") as fh:
            arm = json.load(fh)
        return arm if isinstance(arm, dict) else None
    except (OSError, ValueError):
        return None


def write_arm(session_id, arm):
    folder = _session_dir(session_id)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".arm.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(arm, fh, sort_keys=True)
    os.replace(tmp, os.path.join(folder, "arm.json"))


def _fetch_dir(session_id, agent_id, arm):
    agent = safe_key(agent_id, "main")
    digest = safe_key(str(arm.get("digest") or "").replace("sha256:", ""), "none")[:24]
    key = digest if agent_id else f"{digest}-{safe_key(arm.get('epoch'), 'e0')}"
    return os.path.join(_session_dir(session_id), "fetched", agent, key)


def record_page(session_id, agent_id, arm, page):
    folder = _fetch_dir(session_id, agent_id, arm)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"p{int(page)}"), "a", encoding="utf-8"):
        pass


def fetched_pages(session_id, agent_id, arm):
    try:
        names = os.listdir(_fetch_dir(session_id, agent_id, arm))
    except OSError:
        return set()
    return {int(n[1:]) for n in names if re.fullmatch(r"p\d{1,4}", n)}


def missing_pages(session_id, agent_id, arm):
    total = int(arm.get("pages_total") or 0)
    have = fetched_pages(session_id, agent_id, arm)
    return [p for p in range(1, total + 1) if p not in have]


def _page_of(args):
    if not isinstance(args, dict) or args.get("detail") != "boot":
        return None
    page = args.get("page", 1)
    try:
        page = int(page)
    except (TypeError, ValueError):
        return None
    return page if 1 <= page <= 9999 else None


def _run_sh_is_this_repo(prog, cwd):
    """True when `prog` names this checkout's run.sh (or a worktree of it)."""
    try:
        if prog == "./run.sh":
            base = os.path.realpath(cwd or "")
            candidate = os.path.join(base, "run.sh")
        elif prog.startswith("~/"):
            candidate = os.path.expanduser(prog)
            base = os.path.dirname(candidate)
        else:
            candidate = prog
            base = os.path.dirname(candidate)
        if not os.path.isfile(candidate):
            return False
        mine = os.path.realpath(os.path.join(REPO, "run.sh"))
        if os.path.realpath(candidate) == mine:
            return True
        # A git worktree of this checkout: its .git file points into ours.
        with open(os.path.join(base, ".git"), encoding="utf-8") as fh:
            pointer = fh.read(4096)
        common = os.path.realpath(os.path.join(REPO, ".git"))
        return pointer.startswith("gitdir:") and os.path.realpath(
            pointer.split(":", 1)[1].strip()).startswith(common + os.sep + "worktrees" + os.sep)
    except OSError:
        return False


def classify(tool_name, tool_input, cwd=None):
    """("fetch", page), ("readonly", None) or ("other", None).

    "fetch" with a page is a boot page fetch, to be recorded; "fetch" with
    None is any other standing-context call (allowed, not recorded).
    """
    name = str(tool_name or "")
    if name in REACH_TOOLS:
        return ("readonly", None)
    match = _MCP_VERB.match(name)
    if match and match.group(1) in READ_ONLY_RULE_VERBS:
        if match.group(1) == "standing-context":
            return ("fetch", _page_of(tool_input))
        return ("readonly", None)
    if name == "Bash" and isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "").strip()
        bash = _BASH_FETCH.match(command)
        if bash and _run_sh_is_this_repo(bash.group("prog"), cwd):
            raw = bash.group("json")
            try:
                args = json.loads(raw) if raw is not None else {}
            except ValueError:
                return ("other", None)
            if not isinstance(args, dict):
                return ("other", None)
            return ("fetch", _page_of(args))
    return ("other", None)


def fetch_instructions(pages, digest=None, pages_total=None):
    shown = ", ".join(str(p) for p in pages[:40]) + (" …" if len(pages) > 40 else "")
    first = pages[0] if pages else 1
    head = (f"digest {str(digest)[7:19]}, " if digest else "") + (
        f"{pages_total} page(s)" if pages_total else "")
    return (
        f"RULE BOOT: this context must read the CARR rules ({head}) before any other tool.\n"
        f"Fetch each missing page ({shown}), one call per page, then continue:\n"
        f"  MCP:  standing-context with {{\"detail\":\"boot\",\"page\":{first}}}\n"
        f"  Bash: {os.path.join(REPO, 'run.sh')} call standing-context "
        f"'{{\"detail\":\"boot\",\"page\":{first}}}'\n"
        "Until then only these calls, other standing-context calls, the read-only rule verbs "
        "(applicable-rules, resolve-doctrine-rules, read-doctrine, search-doctrine, doctrine-index, "
        "doctrine-sections) and ToolSearch will run. A fetch that fails still counts, so this can "
        "never lock you out: if the store is unreachable, say RULES UNAVAILABLE before acting.")


# ---------------------------------------------------------------- arming

def _live_page_one(timeout=10):
    """standing-context detail=boot page 1 from the store, or (None, reason)."""
    stub = os.environ.get("CARR_RULE_BOOT_FETCH_STUB")
    if stub is not None:  # TEST HOOK ONLY (ops/rule-boot-gate-selftest.py)
        if stub == "unreachable":
            return None, "unreachable"
        with open(stub, encoding="utf-8") as fh:
            return json.load(fh), None
    try:
        proc = subprocess.run(
            ["python3", os.path.join(REPO, "tools", "call-verb.py"), "standing-context",
             json.dumps({"detail": "boot", "page": 1})],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=REPO)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"unreachable ({type(exc).__name__})"
    out = proc.stdout or ""
    start = out.find("{")
    if proc.returncode != 0 or start < 0:
        return None, f"unreachable (exit {proc.returncode})"
    try:
        return json.loads(out[start:]), None
    except ValueError:
        return None, "unreachable (unparseable response)"


def arm_session(session_id, source, now=None):
    """Arm the gate for a session at SessionStart and return the context text
    (always under 10k characters: SessionStart context is capped there)."""
    response, reason = _live_page_one()
    boot = response.get("rule_boot") if isinstance(response, dict) else None
    arm = {"schema": SCHEMA, "source": str(source or ""), "armed_at": int(now or time.time()),
           "epoch": secrets.token_hex(6)}
    if isinstance(boot, dict) and boot.get("digest") and int(boot.get("pages_total") or 0) >= 1:
        arm.update(status="armed", digest=boot["digest"], pages_total=int(boot["pages_total"]))
        write_arm(session_id, arm)
        return fetch_instructions(list(range(1, arm["pages_total"] + 1)), arm["digest"], arm["pages_total"])
    arm.update(status="unavailable", reason=reason or "the store answered without a rule boot "
               "(the deployed Worker predates standing-context detail=boot)")
    write_arm(session_id, arm)
    return f"{UNAVAILABLE_NOTICE} ({arm['reason']}). No rule boot was loaded for this session."


def verdict(payload):
    """The PreToolUse decision for one tool call: ("allow"|"deny", context_or_reason)."""
    session_id = payload.get("session_id") or payload.get("sessionId")
    agent_id = payload.get("agent_id") or payload.get("agentId")
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    arm = read_arm(session_id)
    kind, page = classify(tool, tool_input, payload.get("cwd"))
    if not arm or arm.get("status") != "armed":
        # NOT ARMED, OR THE STORE WAS UNREACHABLE AT ARMING. There is no page
        # count to hold the context to, so the gate asks for one thing: an
        # ATTEMPT at the boot fetch in this context. The attempt is always
        # allowed, so this can never deadlock, and it makes the model see the
        # store's answer itself instead of acting silently (Jev, 2026-09-26,
        # judged a plain allow here a material bypass: 0.76 unarmed, 0.81
        # unreachable). After the attempt every call is allowed with the notice.
        stand_in = arm if arm else {"digest": "sha256:unarmed", "epoch": "unarmed"}
        notice = UNAVAILABLE_NOTICE if arm else (
            "RULE BOOT NOT ARMED for this session (SessionStart did not arm it): read every page "
            "standing-context {\"detail\":\"boot\"} names before acting, or say the rules are unread.")
        if kind == "fetch":
            record_page(session_id, agent_id, stand_in, page or 1)
            return "allow", None
        if kind == "readonly":
            return "allow", None
        if fetched_pages(session_id, agent_id, stand_in):
            return "allow", notice
        return "deny", (notice + "\nBefore any other tool, ATTEMPT the rule boot fetch once in this context "
                        "(it is always allowed, and a failure still unlocks you):\n" + fetch_instructions([1]))
    if kind == "fetch":
        if page is not None:
            record_page(session_id, agent_id, arm, page)
        return "allow", None
    if kind == "readonly":
        return "allow", None
    missing = missing_pages(session_id, agent_id, arm)
    if not missing:
        return "allow", None
    return "deny", fetch_instructions(missing, arm.get("digest"), arm.get("pages_total"))
