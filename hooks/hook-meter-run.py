#!/usr/bin/env python3
"""hook-meter-run.py — runs a gate, times it, records what happened, decides nothing.

    /usr/bin/env python3 hooks/hook-meter-run.py hooks/guard-unattended.py [args...]

WHY IN-PROCESS AND NOT A SECOND PROCESS. The thing being measured is process
count: 13 hook invocations per Bash tool call, 11 Stop hooks per turn, on a
16GB machine that also runs the agents, where `python3 -c pass` costs 35ms at
best and 75ms at the median. A wrapper that spawned the gate as a child would
double the number it exists to measure and would cost more than everything else
in this file combined. So the gate is executed inside the wrapper's own
interpreter — compiled and exec'd under __main__, with its own argv, its own
stdin bytes and its own __file__ — which is what `python3 gate.py` does anyway,
since a script run as __main__ is never loaded from a .pyc cache either way.

WHAT IS AND IS NOT IMPORTED, because that is the entire performance budget. On
this machine, measured with -X importtime: `traceback` costs ~30ms (it reaches
linecache and tokenize), `re` ~12ms, `json` ~4ms, `runpy` ~2ms. Importing that
set before the gate would have made the measurement instrument cost a third of
the thing it measures. So above the gate there is `sys`, `os`, `io` and `time` —
all resident before this file is read — and nothing else. `traceback` is
imported inside the crash handler, `json` after the gate has finished (by which
point the gate has almost always imported it already, making it free), and `re`
never: the one marker this file looks for is found with str.find. Measured cost
of the wrapper itself is 0.7–1.0ms against a 38–65ms floor for a single hook,
and it is recorded on every line as meter_ms so the claim stays checkable
instead of becoming folklore.

THE ONE INVARIANT: THE DECISION IS UNTOUCHED.
  · exit code — SystemExit is caught and re-raised as the same code; a gate
    that returns without exiting exits 0; an escaping exception prints its
    traceback to stderr and exits 1, which is what a bare `python3 gate.py`
    would have done. The traceback text carries two extra wrapper frames at the
    top; the exception and its own frames are unchanged.
  · stdout / stderr — written through to the real streams as they are produced.
    The copy kept for classification is a side effect, never a substitute, and
    it is bounded so a chatty gate cannot make the meter the memory problem.
  · stdin — read once here and handed to the gate as a fresh TextIOWrapper over
    the same bytes, so a gate reading sys.stdin, sys.stdin.buffer or
    json.load(sys.stdin) sees exactly what it saw before.
  · the gate's own exceptions — never swallowed. Everything the METER does is
    wrapped; nothing the GATE does is.

AND THE MEASUREMENT IS NEVER LOAD-BEARING. hook_meter is imported inside a
try, and every use of it is guarded, because the alternative was proven bad on
2026-08-23 while this was being built: with a bare import, deleting or breaking
hook_meter.py made a gate die at import and exit 1 — which for guard-unattended
means a refusal silently stops happening. So the two things that WOULD change a
verdict if they failed — replacing stdin, and passing output through — are done
here with `io` alone and depend on nothing. If hook_meter is gone, this file
still runs the gate correctly and simply records nothing. Being unable to
measure is never a reason to change a verdict.

WHAT ONE LINE HOLDS: event, hook, live|fixture|unclassified, elapsed_ms
(monotonic, the gate only), meter_ms, allow|deny|ask|error, exit code, whether a
Stop gate reopened the turn, and a deny class where the gate exposes one — plus
the first line of its refusal, which is the class every gate here already has in
practice even though none of them declares one yet.

REOPENS ARE THE EXPENSIVE EVENT, and that is grok's flag from the 2026-08-23
council: a Stop gate that blocks does not merely cost 15ms of Python, it hands
the turn back to the model and spends LLM tokens. The standing constraint is
that nothing may increase token usage in steady state — so an enforcement stack
that reopens turns on false positives is that constraint being violated by the
thing enforcing it. Reopens are therefore counted per gate per day rather than
folded into a deny total.

Fixtures: ops/hook-meter-selftest.py
"""

import time as _time
_T0 = _time.monotonic() * 1000.0        # first statement: the meter's own clock

import io                               # noqa: E402 — all three are resident
import os                               # noqa: E402   before this file is read
import sys                              # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import hook_meter                   # noqa: E402
except Exception:                       # recording is optional; the gate is not
    hook_meter = None                   # type: ignore[assignment]

# A gate may declare a stable class for a refusal by printing this line once.
# None do today, so the field stays null rather than being inferred from prose.
DENY_MARKER = "DENY-CLASS:"

STOP_EVENTS = ("Stop", "SubagentStop")

MAX_FIELD = 300


class Tee(io.TextIOBase):
    """Pass output through untouched while keeping a bounded copy to classify.

    The copy is how a decision carried in JSON on stdout (a Stop gate's
    {"decision":"block"}) gets counted without the meter re-deciding anything.
    The pass-through is the part that must not fail: the harness reads the real
    stream, so every write reaches it and a failure to record stays silent.
    """

    def __init__(self, real, cap=64 * 1024):
        self._real = real
        self._buf = []
        self._size = 0
        self._cap = cap

    def write(self, s):
        try:
            if self._size < self._cap:
                self._buf.append(s)
                self._size += len(s)
        except Exception:
            pass
        return self._real.write(s)

    def flush(self):
        return self._real.flush()

    def isatty(self):
        try:
            return self._real.isatty()
        except Exception:
            return False

    def fileno(self):
        return self._real.fileno()

    @property
    def captured(self):
        try:
            return "".join(self._buf)
        except Exception:
            return ""


def replacement_stdin(raw):
    """A re-readable stdin carrying `raw`, with a .buffer like the real one.

    The wrapper reads the payload so it can name the event; the gate then has to
    read the same bytes. A bare StringIO would break any gate reaching for
    sys.stdin.buffer, so the substitute is a real TextIOWrapper — and it is
    built with `io` here rather than in hook_meter, because a gate that receives
    an empty stdin reaches a DIFFERENT verdict (most fail open and allow). This
    one line is the difference between instrumentation and an outage.
    """
    return io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace")


def _clip(value):
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= MAX_FIELD else text[:MAX_FIELD] + "…"


def _scan_field(raw, key):
    """The string value of a top-level scalar key, without parsing the payload.

    A Write payload carries the entire file being written and a Stop payload can
    reference a large transcript; json.loads on those costs real time to
    retrieve three short strings that are always plain, escape-free scalars (an
    event name, a tool name, a uuid). This finds them with str.find, and returns
    None the moment anything is unusual so the caller falls back to a real parse
    — the fast path is an optimisation, never a second parser whose
    disagreements with json would go unnoticed.
    """
    try:
        needle = b'"' + key + b'":'
        idx = raw.find(needle)
        if idx < 0:
            needle = b'"' + key + b'": '
            idx = raw.find(needle)
            if idx < 0:
                return None
        rest = raw[idx + len(needle):idx + len(needle) + 128].lstrip()
        if not rest.startswith(b'"'):
            return None
        end = rest.find(b'"', 1)
        if end < 0:
            return None
        value = rest[1:end]
        if b"\\" in value:
            return None
        return value.decode("utf-8", "replace")
    except Exception:
        return None


def _payload_facts(raw):
    """Event name, tool and session id from the harness payload. Never raises.

    Called AFTER the gate has run: nothing above needs these, and by then the
    gate has usually imported json itself, so the fallback costs little.
    """
    facts = {"event": None, "tool": None, "session": None}
    if not raw:
        return facts
    try:
        facts["event"] = _scan_field(raw, b"hook_event_name")
        facts["tool"] = _scan_field(raw, b"tool_name")
        facts["session"] = _scan_field(raw, b"session_id")
        if facts["event"]:
            return facts
        import json
        data = json.loads(raw.decode("utf-8", "replace"))
        if isinstance(data, dict):
            facts["event"] = data.get("hook_event_name") or data.get("hookEventName")
            facts["tool"] = data.get("tool_name") or data.get("toolName")
            facts["session"] = data.get("session_id") or data.get("sessionId")
    except Exception:
        pass
    return facts


def _decision_from_output(text):
    """The gate's own JSON verdict, when it published one. None otherwise.

    Exit codes carry most verdicts here, but the harness also honours structured
    output, and a gate that says {"decision":"block"} with exit 0 has blocked
    just as hard as one that exited 2. Reading it is how the meter stays right
    if a gate changes spellings later.
    """
    try:
        stripped = (text or "").strip()
        if not stripped.startswith("{"):
            return None
        import json
        data = json.loads(stripped)
        if not isinstance(data, dict):
            return None
        specific = data.get("hookSpecificOutput")
        if isinstance(specific, dict):
            decision = specific.get("permissionDecision")
            if decision in ("deny", "ask", "allow"):
                return decision
        decision = data.get("decision")
        if decision in ("block", "deny"):
            return "deny"
        if decision == "approve":
            return "allow"
        if data.get("continue") is False:
            return "deny"
    except Exception:
        pass
    return None


def _deny_class(text):
    """The declared class, found without importing re."""
    try:
        idx = (text or "").find(DENY_MARKER)
        if idx < 0:
            return None
        tail = (text[idx + len(DENY_MARKER):]).split()
        token = tail[0][:64] if tail else ""
        return token if token and all(c.isalnum() or c in "._-" for c in token) else None
    except Exception:
        return None


def _headline(text):
    """First non-empty line of a refusal — the de-facto class gates already have."""
    try:
        for line in (text or "").splitlines():
            line = line.strip()
            if line:
                return line
    except Exception:
        pass
    return None


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write("hook-meter-run.py: no gate named\n")
        return 1

    target = argv[0]
    if not os.path.isabs(target):
        target = os.path.join(REPO, target)

    # ── setup. The two steps that would change a verdict if they failed —
    #    handing the gate its stdin, and passing its output through — use io
    #    alone and cannot be affected by the recording layer being absent.
    raw = b""
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        raw = b""
    try:
        sys.stdin = replacement_stdin(raw)
    except Exception:
        pass
    if hook_meter is not None:
        try:
            # Before the gate runs, so the gate's OWN human-readable log line
            # lands in the same stream as the telemetry line about it.
            hook_meter.mark_live()
        except Exception:
            pass

    real_out, real_err = sys.stdout, sys.stderr
    try:
        sys.stdout = Tee(real_out)
        sys.stderr = Tee(real_err)
    except Exception:
        sys.stdout, sys.stderr = real_out, real_err

    saved_argv = sys.argv
    sys.argv = [target] + argv[1:]
    scope = {
        "__name__": "__main__",
        "__file__": target,
        "__doc__": None,
        "__package__": None,
        "__loader__": None,
        "__spec__": None,
        "__cached__": None,
    }

    # ── the gate, unguarded on purpose ──
    started = _time.monotonic() * 1000.0
    code = 0
    crashed = False
    try:
        with open(target, "rb") as fh:
            source = fh.read()
        exec(compile(source, target, "exec"), scope)     # noqa: S102 — this IS the gate
    except SystemExit as exc:
        value = exc.code
        code = 0 if value is None else (value if isinstance(value, int) else 1)
        if not isinstance(value, (int, type(None))):
            sys.stderr.write(f"{value}\n")
    except BaseException:                       # noqa: BLE001 — mimic `python3 gate.py`
        import traceback
        traceback.print_exc()
        code = 1
        crashed = True
    elapsed = _time.monotonic() * 1000.0 - started

    # ── recording, which cannot change what already happened above ──
    captured_out = captured_err = ""
    try:
        captured_out = getattr(sys.stdout, "captured", "")
        captured_err = getattr(sys.stderr, "captured", "")
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    sys.stdout, sys.stderr = real_out, real_err
    sys.argv = saved_argv

    if hook_meter is None:
        return code
    try:
        facts = _payload_facts(raw)
        published = _decision_from_output(captured_out)
        if crashed or code not in (0, 2):
            outcome = "error"
        elif code == 2:
            outcome = "deny"
        elif published:
            outcome = published
        else:
            outcome = "allow"

        event = facts["event"] or ""
        record = {
            "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "event": event or None,
            "hook": os.path.basename(target),
            "arg": (os.path.basename(argv[1]) if len(argv) > 1 else None),
            "tool": facts["tool"],
            "session": facts["session"],
            "elapsed_ms": round(elapsed, 2),
            "outcome": outcome,
            "exit": code,
            "reopen": bool(event in STOP_EVENTS and outcome == "deny"),
            "deny_class": _deny_class(captured_err) or _deny_class(captured_out),
            "deny_headline": (_clip(_headline(captured_err))
                              if outcome in ("deny", "ask", "error") else None),
            "pid": os.getpid(),
        }
        record["meter_ms"] = round(_time.monotonic() * 1000.0 - _T0 - elapsed, 2)
        hook_meter.emit(REPO, record)
    except Exception:
        pass

    return code


if __name__ == "__main__":
    # os._exit is deliberate: a gate may leave a non-daemon thread or an atexit
    # handler behind and the harness is waiting on this process. The streams are
    # flushed above; nothing else here owns unflushed state.
    try:
        rc = main()
    except BaseException:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        rc = 1
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(rc if isinstance(rc, int) else 1)
