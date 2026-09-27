"""rule_boot_gate.py — state and classification for the gated rule boot.

WHAT THE GATE IS. Joe asked for 100% recall on relevant rules. Nothing put the
rules in front of a context before it acted, so the design Jev chose (p=1.00)
is a gate: every session and every subagent must fetch every page of
standing-context `detail: "boot"` (mcp-server/src/rule-boot.js: the full text
of the always-on rules plus a one-line index of every active rule) before any
other tool call runs. hooks/rule-boot-gate.py enforces it on PreToolUse and
reads each fetch's answer on PostToolUse; hooks/gate-integrity.py arms (and
re-arms) it at SessionStart. They all import this module so the state layout,
the fetch-call grammar and the notices live in one place.

DATABASE AND CODE ONLY. The state here holds the digest, the page count and
which pages a context has fetched. It never holds rule text.

STATE LAYOUT (under out/rule-boot-gate/, gitignored, per machine):
    <session>/arm.json                    status (armed | unavailable |
                                          not_deployed), digest, pages_total,
                                          epoch
    <session>/fetched/<agent>/<key>/      one directory per context and digest
        p<N>        page N's fetch was ATTEMPTED (PreToolUse)
        c<N>        page N came back as a real boot page (PostToolUse)
        failed      a fetch came back as an error: the store is unreachable
        unsupported the Worker rejected detail=boot: not deployed yet
        d<H>-<rnd>  one deny, made when H pages were confirmed
Marker files, not a read-modify-write JSON, because a model often fetches the
pages in parallel and parallel hooks would otherwise lose each other's pages.
<key> is the digest for a subagent and digest+epoch for the main context, so
a SessionStart re-arm (startup, resume, clear, compact, fork) makes the main
context fetch again, and a digest change re-gates every context.

WHEN THE DIGEST MOVES MID-SESSION. Every boot page carries the corpus digest
and page count. When a fetch in any context returns a digest or page count
that differs from the arm, PostToolUse re-arms the session with it, so every
context of the session is held again at its next call until it has the new
pages. There is no timer and no network in the gate: a change is seen at
SessionStart or at the next boot fetch by any context in the session (every
new subagent makes one). A session whose contexts are all complete and that
starts no subagent keeps the digest it has until one of those happens.

NEVER A LOCKOUT. Reviewed on PR #1328; each path is a selftest case:
  * The fetch, the read-only rule verbs and ToolSearch are always allowed.
  * A DENY IS ONLY EVER MADE ON STATE THE HOOK WROTE. Every deny first writes
    its own d-marker; if that write fails (folder unwritable, disk or inodes
    full) the call is allowed with STATE_UNWRITABLE_NOTICE instead. A missing
    marker the hook could not write can therefore never become a deny.
  * DENY CAP: after DENY_CAP denies in one context with no page confirmed in
    between, every call is allowed with CAP_NOTICE. That covers contexts with
    no tool that can fetch (a Read/Edit-only subagent) and any lockout nobody
    foresaw. Subagent types known to have no fetch tool are never denied.
  * STORE UNREACHABLE (at arming, or a later fetch that errors, or a fetch
    that never answered within PENDING_GRACE_S): after that one attempt the
    context is allowed on every call with UNAVAILABLE_NOTICE.
  * WORKER NOT YET DEPLOYED (standing-context rejects detail=boot): its own
    status and NOT_DEPLOYED_NOTICE, shown once per context; nothing is held.

A LIBRARY: no shebang, no main guard (see ops/jev_judge.py's docstring on the
SCAC inventory).
"""
import errno
import json
import os
import re
import secrets
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = "carr-rule-boot-gate/v1"
BOOT_SCHEMA = "carr-rule-boot/v1"

DENY_CAP = 3
PENDING_GRACE_S = 45

# The CARR connector's MCP prefixes: the local server, the records alias and
# the claude.ai account connector (by name and by this account's connector
# id). Only these count as the fetch or as a pre-boot read; the same verb name
# on any other server is an ordinary tool. A partner whose claude.ai connector
# has a different id fetches through the Bash form, which is always allowed.
CARR_MCP_PREFIXES = ("mcp__carr__", "mcp__carr_records__", "mcp__claude_ai_CARR_Record_Layer__",
                     "mcp__b36e17b6-7e3b-4e65-b890-21f21d538440__")

# Read-only rule verbs a context may call before its boot is complete. They
# read rules and doctrine and write nothing.
READ_ONLY_RULE_VERBS = frozenset({
    "standing-context", "applicable-rules", "resolve-doctrine-rules",
    "read-doctrine", "search-doctrine", "doctrine-index", "doctrine-sections",
})
# Tools that are needed to REACH the fetch: in Claude Code, MCP tool schemas
# are deferred and must be loaded with ToolSearch before they can be called.
REACH_TOOLS = frozenset({"ToolSearch"})
# Built-in subagent types with neither Bash nor an MCP tool. They can never
# fetch, so they are never denied (the deny cap covers custom ones).
TOOLLESS_AGENT_TYPES = frozenset({"statusline-setup"})

_BASH_FETCH = re.compile(
    r"^(?P<prog>\./run\.sh|~/carr-system/run\.sh|/[^\s'\"`$;&|<>()\\]+/run\.sh)"
    r"\s+call\s+standing-context(?:\s+'(?P<json>[^']*)')?\s*$")
_BOOT_UNSUPPORTED = re.compile(
    r"value_not_in_declared_vocabulary[\s\S]{0,400}\"?(detail|page)\"?"
    r"|\"?(detail|page)\"?[\s\S]{0,400}value_not_in_declared_vocabulary"
    r"|(unknown|unexpected|undeclared|additional)[\s\S]{0,80}\"page\"")

# The four notices. Wording reviewed by Jev (semantic_creation,
# kind rule-boot-gate-notice-texts): each says what happened and what to do.
UNAVAILABLE_NOTICE = (
    "RULES UNAVAILABLE: the CARR store did not answer the rule boot fetch, so this context has "
    "not read the CARR rules. The gate will not hold your tool calls. Do this: (1) tell the user "
    "the rules are unavailable before you act on anything that depends on them; (2) retry "
    "standing-context {\"detail\":\"boot\",\"page\":1} later and read its pages if it answers.")
NOT_DEPLOYED_NOTICE = (
    "RULE BOOT NOT DEPLOYED YET: the CARR store answered, but its Worker does not serve "
    "standing-context detail=boot yet. This is expected until that release ships and is not "
    "an outage: keep working normally, and use applicable-rules for the task in hand. "
    "Nothing is blocked.")
_FETCH_BOTH = (
    "standing-context {{\"detail\":\"boot\",\"page\":1}} (CARR MCP) or `"
    + os.path.join(REPO, "run.sh").replace("{", "{{").replace("}", "}}")
    + " call standing-context '{{\"detail\":\"boot\",\"page\":1}}'` (Bash), then each "
    "further page the reply lists")
CAP_NOTICE = (
    "RULES UNREAD: you tried other tools {n} times without fetching the CARR rules, so the rule "
    "gate has stopped blocking you. You have NOT read the CARR rules. Next: fetch them now with "
    + _FETCH_BOTH + ". If you have neither tool, carry on and write \"CARR rules not read\" in "
    "your result.")
STATE_UNWRITABLE_NOTICE = (
    "RULE BOOT STATE UNWRITABLE: the rule gate cannot save its files on this machine ({why}, "
    "for example a full disk or a read-only folder), so it has stopped blocking and cannot "
    "tell whether this context has read the CARR rules. Next: (1) unless the CARR rule boot "
    "pages are already in this conversation, fetch them now with " + _FETCH_BOTH + "; if you "
    "have neither tool, carry on and write \"CARR rules not read\" in your result. (2) Tell "
    "the user the rule gate's state folder cannot be written.")
UNARMED_NOTICE = (
    "RULE BOOT NOT ARMED: SessionStart did not arm the rule gate for this session. Read every "
    "page standing-context {\"detail\":\"boot\"} names before acting, or say the rules are unread.")


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
    tmp = os.path.join(folder, f".arm.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(arm, fh, sort_keys=True)
    os.replace(tmp, os.path.join(folder, "arm.json"))


def _stand_in(arm):
    """The keying arm for a context whose session has no usable page count."""
    return arm if arm and arm.get("status") == "armed" else {
        "digest": "sha256:" + str((arm or {}).get("status") or "unarmed"),
        "epoch": (arm or {}).get("epoch") or "unarmed"}


def _fetch_dir(session_id, agent_id, arm):
    agent = safe_key(agent_id, "main")
    digest = safe_key(str(arm.get("digest") or "").replace("sha256:", ""), "none")[:24]
    key = digest if agent_id else f"{digest}-{safe_key(arm.get('epoch'), 'e0')}"
    return os.path.join(_session_dir(session_id), "fetched", agent, key)


def _touch(folder, name):
    """Create folder/name. Returns None on success, else the OSError text."""
    try:
        os.makedirs(folder, exist_ok=True)
        fd = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT, 0o644)
        os.close(fd)
        return None
    except OSError as exc:
        return errno.errorcode.get(exc.errno, "OSError") if exc.errno else str(exc)


def _markers(folder):
    try:
        return os.listdir(folder)
    except OSError:
        return []


def record_page(session_id, agent_id, arm, page):
    return _touch(_fetch_dir(session_id, agent_id, arm), f"p{int(page)}")


def _pages(names, prefix):
    return {int(n[1:]) for n in names if re.fullmatch(prefix + r"\d{1,4}", n)}


def fetched_pages(session_id, agent_id, arm):
    """Pages this context has ATTEMPTED under this arm."""
    return _pages(_markers(_fetch_dir(session_id, agent_id, arm)), "p")


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


def _carr_verb(name):
    for prefix in CARR_MCP_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return None


def classify(tool_name, tool_input, cwd=None):
    """("fetch", page), ("readonly", None) or ("other", None).

    "fetch" with a page is a boot page fetch, to be recorded; "fetch" with
    None is any other standing-context call (allowed, not recorded).
    """
    name = str(tool_name or "")
    if name in REACH_TOOLS:
        return ("readonly", None)
    verb = _carr_verb(name)
    if verb in READ_ONLY_RULE_VERBS:
        if verb == "standing-context":
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
        f"  MCP (CARR connector):  standing-context with {{\"detail\":\"boot\",\"page\":{first}}}\n"
        f"  Bash: {os.path.join(REPO, 'run.sh')} call standing-context "
        f"'{{\"detail\":\"boot\",\"page\":{first}}}'\n"
        "Until then only these calls, other standing-context calls, the read-only rule verbs "
        "(applicable-rules, resolve-doctrine-rules, read-doctrine, search-doctrine, doctrine-index, "
        "doctrine-sections) and ToolSearch will run. This can never lock you out: a fetch that "
        f"fails unlocks you with a notice, and after {DENY_CAP} holds without a fetch the gate "
        "stops holding this context.")


# ---------------------------------------------------------------- answers

def _strings(value, out, depth=0):
    if depth > 6 or len(out) > 64:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out, depth + 1)


def _find_boot(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, dict):
        boot = value.get("rule_boot")
        if isinstance(boot, dict) and boot.get("digest"):
            return boot
        if value.get("schema") == BOOT_SCHEMA and value.get("digest"):
            return value
        for v in value.values():
            found = _find_boot(v, depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for v in value:
            found = _find_boot(v, depth + 1)
            if found:
                return found
    return None


def read_answer(response):
    """What a boot fetch came back as: ("boot", rule_boot) | ("unsupported", None)
    | ("failed", None). Parses a Bash result or an MCP result; never raises."""
    boot = _find_boot(response)
    if boot:
        return "boot", boot
    texts = []
    _strings(response, texts)
    decoder = json.JSONDecoder()
    for text in texts:
        if "rule_boot" not in text and BOOT_SCHEMA not in text:
            continue
        for start in [m.start() for m in re.finditer(r"\{", text)][:8]:
            try:
                parsed, _ = decoder.raw_decode(text, start)
            except ValueError:
                continue
            boot = _find_boot(parsed)
            if boot:
                return "boot", boot
    if any(_BOOT_UNSUPPORTED.search(t[:20000]) for t in texts):
        return "unsupported", None
    return "failed", None


# ---------------------------------------------------------------- arming

def _live_page_one(timeout=10):
    """(response, None) or (None, reason); reason starts with "not_deployed"
    when the store answered and rejected detail=boot."""
    stub = os.environ.get("CARR_RULE_BOOT_FETCH_STUB")
    if stub is not None:  # TEST HOOK ONLY (ops/rule-boot-gate-selftest.py)
        if stub in ("unreachable", "not_deployed"):
            return None, stub
        with open(stub, encoding="utf-8") as fh:
            return json.load(fh), None
    try:
        proc = subprocess.run(
            ["python3", os.path.join(REPO, "tools", "call-verb.py"), "standing-context",
             json.dumps({"detail": "boot", "page": 1})],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=REPO)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"unreachable ({type(exc).__name__})"
    kind, boot = read_answer({"stdout": proc.stdout or "", "stderr": proc.stderr or ""})
    if kind == "boot":
        return {"rule_boot": boot}, None
    if kind == "unsupported":
        return None, "not_deployed (the deployed Worker rejects standing-context detail=boot)"
    return None, f"unreachable (exit {proc.returncode})"


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
    if str(reason or "").startswith("not_deployed"):
        arm.update(status="not_deployed", reason=reason)
        write_arm(session_id, arm)
        return NOT_DEPLOYED_NOTICE
    arm.update(status="unavailable", reason=reason or "unreachable")
    write_arm(session_id, arm)
    return f"{UNAVAILABLE_NOTICE} ({arm['reason']})"


# ---------------------------------------------------------------- verdicts

def _toolless(payload):
    kind = str(payload.get("agent_type") or payload.get("agentType") or "")
    return bool(payload.get("agent_id") or payload.get("agentId")) and kind in TOOLLESS_AGENT_TYPES


def _hold(folder, confirmed, reason):
    """Deny, but only on state written now; capped per context."""
    why = _touch(folder, f"d{confirmed}-{secrets.token_hex(4)}")
    if why:
        return "allow", STATE_UNWRITABLE_NOTICE.format(why=why)
    held = sum(1 for n in _markers(folder) if n.startswith(f"d{confirmed}-"))
    if held > DENY_CAP:
        return "allow", CAP_NOTICE.format(n=DENY_CAP)
    return "deny", reason


def _once(folder, name, notice):
    """A notice shown once per context (again if the marker cannot be written)."""
    if name in _markers(folder):
        return None
    _touch(folder, name)
    return notice


def verdict(payload, now=None):
    """The PreToolUse decision for one tool call: ("allow"|"deny", context_or_reason)."""
    session_id = payload.get("session_id") or payload.get("sessionId")
    agent_id = payload.get("agent_id") or payload.get("agentId")
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    arm = read_arm(session_id)
    key = _stand_in(arm)
    folder = _fetch_dir(session_id, agent_id, key)
    kind, page = classify(tool, tool_input, payload.get("cwd"))
    if kind == "fetch":
        if page is not None:
            why = record_page(session_id, agent_id, key, page)
            if why:
                return "allow", STATE_UNWRITABLE_NOTICE.format(why=why)
        return "allow", None
    if kind == "readonly":
        return "allow", None
    status = (arm or {}).get("status")
    if status == "not_deployed":
        return "allow", _once(folder, "shown-not-deployed", NOT_DEPLOYED_NOTICE)
    names = _markers(folder)
    if "unsupported" in names:
        return "allow", _once(folder, "shown-not-deployed", NOT_DEPLOYED_NOTICE)
    if _toolless(payload):
        return "allow", _once(folder, "shown-toolless", CAP_NOTICE.format(n=0))
    attempted = _pages(names, "p")
    if status != "armed":
        # NOT ARMED, OR THE STORE WAS UNREACHABLE AT ARMING. There is no page
        # count to hold the context to, so the gate asks for one thing: an
        # ATTEMPT at the boot fetch in this context (Jev, 2026-09-26: a plain
        # allow here is a material bypass). After it, every call is allowed
        # with the notice.
        notice = UNAVAILABLE_NOTICE if arm else UNARMED_NOTICE
        if attempted or "failed" in names:
            return "allow", notice
        return _hold(folder, 0, notice + "\nBefore any other tool, ATTEMPT the rule boot fetch once in "
                     "this context (it is always allowed, and a failure still unlocks you):\n"
                     + fetch_instructions([1]))
    if "failed" in names:
        return "allow", UNAVAILABLE_NOTICE
    confirmed = _pages(names, "c")
    total = int(arm.get("pages_total") or 0)
    missing = [p for p in range(1, total + 1) if p not in confirmed]
    if not missing:
        return "allow", None
    # A page attempted but never answered: PostToolUse writes c<N> or failed,
    # so silence past the grace means the fetch failed without a result.
    now = now or time.time()
    for p in missing:
        if p in attempted:
            try:
                age = now - os.path.getmtime(os.path.join(folder, f"p{p}"))
            except OSError:
                continue
            if age > PENDING_GRACE_S:
                return "allow", UNAVAILABLE_NOTICE
    return _hold(folder, len(confirmed), fetch_instructions(missing, arm.get("digest"), total))


def observe(payload):
    """PostToolUse on a boot fetch: record what came back, re-arm the session
    on a new digest or page count. Returns a notice for the context, or None."""
    session_id = payload.get("session_id") or payload.get("sessionId")
    agent_id = payload.get("agent_id") or payload.get("agentId")
    kind, page = classify(payload.get("tool_name") or payload.get("toolName") or "",
                          payload.get("tool_input") or payload.get("toolInput") or {},
                          payload.get("cwd"))
    if kind != "fetch" or page is None:
        return None
    response = payload.get("tool_response")
    if response is None:
        response = payload.get("toolResponse")
    if response is None:
        response = payload.get("error")
    answer, boot = read_answer(response)
    arm = read_arm(session_id)
    if answer == "boot":
        total = int(boot.get("pages_total") or 0)
        digest = str(boot.get("digest") or "")
        if total >= 1 and digest and (not arm or arm.get("status") != "armed"
                                      or arm.get("digest") != digest
                                      or int(arm.get("pages_total") or 0) != total):
            arm = {**(arm or {}), "schema": SCHEMA, "status": "armed", "digest": digest,
                   "pages_total": total, "rearmed_by": "fetch", "armed_at": int(time.time())}
            arm.setdefault("epoch", secrets.token_hex(6))
            arm.pop("reason", None)
            try:
                write_arm(session_id, arm)
            except OSError:
                return None
        key = _stand_in(arm)
        folder = _fetch_dir(session_id, agent_id, key)
        _touch(folder, f"p{page}")
        _touch(folder, f"c{page}")
        return None
    folder = _fetch_dir(session_id, agent_id, _stand_in(arm))
    if answer == "unsupported":
        _touch(folder, "unsupported")
        return NOT_DEPLOYED_NOTICE
    _touch(folder, "failed")
    return UNAVAILABLE_NOTICE
