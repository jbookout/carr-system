#!/usr/bin/env python3
"""LIVE test #3: does a DEFAULT-configured session take an unauthenticated turn?

Test #2 proved the peer turn reaches the model. But its target was booted with
`--messaging-socket-path`, and on that path the session's own banner says:

    Inject messages (auth line optional HERE): { echo '{"type":"auth","token":
    "'"$CLAUDE_CODE_MESSAGING_TOKEN"'"}'; echo '{"type":"user",...}'; } | socat ...

"here" is doing load-bearing work. A partner line will not run against sockets
we hand-placed with a dev flag — it has to run against the socket a normal
session binds by itself (`/tmp/cc-socks/<pid>.sock`, which is what all of Joe's
real sessions are). So this test boots a labeled throwaway with NO
`--messaging-socket-path`, lets it bind its own pid socket, and asks two
questions in order:

    1. does an injection with NO auth line reach the model?
    2. does the same injection reach it when preceded by the auth frame?

SAFETY, and it is not decorative: this test discovers a socket by pid instead
of being told one, so before it writes a single byte it requires that the
socket's pid be a `claude` process whose cwd is this test's own throwaway
directory. Joe's live sessions sit in the same directory. Anything ambiguous
aborts without sending.

Run:  python3 spikes/partner-line-78/test_auth_gate_live.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from inject import ReplyListener, inject_keepalive, is_live, peer_turn  # noqa: E402

OUT = Path("/Users/booko/carr-system/out")
SOCK_DIR = Path("/tmp/cc-socks")
SESSION = "spike78-auth"
CWD = Path("/tmp/spike78-auth-cwd")
REAL_CWD = "/private/tmp/spike78-auth-cwd"  # /tmp is a symlink on macOS
REPLY_SOCK = str(SOCK_DIR / "spike78-auth-reply.sock")
TRANSCRIPT_DIR = Path.home() / ".claude/projects/-private-tmp-spike78-auth-cwd"

BOOT_TIMEOUT_S = 120
SEED_TIMEOUT_S = 120
ANSWER_TIMEOUT_S = 60

SEED = (
    "You are a labeled throwaway session for a messaging spike. "
    "Reply with exactly READY and nothing else."
)


def tmux(*args: str, check: bool = True) -> str:
    r = subprocess.run(["/opt/homebrew/bin/tmux", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def pane() -> str:
    return tmux("capture-pane", "-p", "-J", "-t", SESSION, check=False)


def wait_for(pred, timeout: float, what: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.5)
    print(f"  timeout waiting for {what} after {timeout}s", file=sys.stderr)
    return False


def send_line(text: str) -> None:
    for _ in range(4):
        tmux("send-keys", "-t", SESSION, "-l", text)
        time.sleep(1.5)
        if text[:24] in pane():
            break
        time.sleep(2)
    tmux("send-keys", "-t", SESSION, "Enter")


def socks() -> set[Path]:
    return set(SOCK_DIR.glob("*.sock"))


def proc_cwd(pid: int) -> str:
    r = subprocess.run(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
    )
    for line in r.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def proc_cmd(pid: int) -> str:
    r = subprocess.run(
        ["/bin/ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True
    )
    return r.stdout.strip()


def verify_ours(sock: Path) -> tuple[bool, str]:
    """Refuse to send unless this socket demonstrably belongs to OUR throwaway."""
    try:
        pid = int(sock.stem)
    except ValueError:
        return False, f"{sock.name}: not a pid-named socket"
    cmd, cwd = proc_cmd(pid), proc_cwd(pid)
    if "claude" not in cmd:
        return False, f"pid {pid} is not a claude process: {cmd!r}"
    if cwd != REAL_CWD:
        return False, f"pid {pid} cwd is {cwd!r}, not our throwaway {REAL_CWD!r}"
    return True, f"pid {pid} is claude in {cwd} (ours)"


def transcript_turns() -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    if not TRANSCRIPT_DIR.is_dir():
        return turns
    for f in sorted(TRANSCRIPT_DIR.glob("*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = (rec.get("message") or {}).get("content")
            text = (
                " ".join(b.get("text", "") for b in c if isinstance(b, dict))
                if isinstance(c, list)
                else (c if isinstance(c, str) else "")
            )
            if text.strip():
                turns.append((rec.get("type", "?"), text))
    return turns


def assistant_said(nonce: str) -> bool:
    return any(r == "assistant" and nonce in t for r, t in transcript_turns())


def inject_raw(sock_path: str, lines: list[dict]):
    """Write several NDJSON frames on ONE connection, keeping it open."""
    import socket as _s

    blob = "".join(json.dumps(o, separators=(",", ":")) + "\n" for o in lines)
    c = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
    c.settimeout(5.0)
    c.connect(sock_path)
    c.sendall(blob.encode())
    return c


def attempt(sock_path: str, label: str, token: str | None, debug_log: Path) -> dict:
    nonce = "SPIKE78-" + uuid.uuid4().hex[:12].upper()
    payload = peer_turn(
        f"Partner-line spike 78 auth gate. Reply now with exactly {nonce} and "
        "nothing else.",
        reply_to=REPLY_SOCK,
    )
    frames = ([{"type": "auth", "token": token}] if token else []) + [payload]
    print(f"\n[{label}] INJECT -> " + " + ".join(json.dumps(f) for f in frames))
    before = len(debug_log.read_text(errors="replace").splitlines())
    conn = inject_raw(sock_path, frames)
    try:
        ok = wait_for(lambda: assistant_said(nonce), ANSWER_TIMEOUT_S, f"model to say {nonce}")
    finally:
        conn.close()
    new = debug_log.read_text(errors="replace").splitlines()[before:]
    interesting = [
        l.strip()
        for l in new
        if any(k in l for k in ("uds-messaging", "cross-session-inbound", "peer-cred", "auth"))
    ]
    for l in interesting[:12]:
        print("    " + l)
    return {
        "label": label,
        "auth_line_sent": bool(token),
        "nonce": nonce,
        "payload": payload,
        "reached_model": ok,
        "debug": interesting,
    }


def main() -> int:
    debug_log = OUT / "spike78-auth-debug.log"
    token = "spike78-" + uuid.uuid4().hex
    report = {"token_env_set": True, "socket_flag_used": False}

    tmux("kill-session", "-t", SESSION, check=False)
    CWD.mkdir(exist_ok=True)
    debug_log.write_bytes(b"")
    listener = ReplyListener(REPLY_SOCK).start()

    before = socks()
    print(f"pre-existing sockets (NOT ours, hands off): {sorted(p.name for p in before)}")

    try:
        # No --messaging-socket-path. The session picks its own pid socket, the
        # same way every one of Joe's real sessions does.
        shell = (
            f"CLAUDE_CODE_MESSAGING_TOKEN={token} claude --safe-mode "
            f"--debug-to-stderr 2>{debug_log}"
        )
        tmux("new-session", "-d", "-s", SESSION, "-x", "200", "-y", "50", "-c", str(CWD), shell)

        # A never-before-seen cwd gets the workspace-trust dialog first.
        # A never-before-seen cwd gets the workspace-trust dialog first, and it
        # drops keystrokes sent while it is still painting — so answer it with
        # the explicit selector as well as Enter, and keep re-checking.
        for i in range(20):
            if "trust this folder" in pane() or "Do you trust the files" in pane():
                print(f"  trust dialog present -> accepting (attempt {i + 1})")
                if i:
                    tmux("send-keys", "-t", SESSION, "-l", "1")
                    time.sleep(1)
                tmux("send-keys", "-t", SESSION, "Enter")
                time.sleep(3)
                continue
            break
        if not wait_for(
            lambda: "shift+tab to cycle" in pane() or "? for shortcuts" in pane(),
            BOOT_TIMEOUT_S,
            "the TUI input box",
        ):
            print("FAIL: TUI never came up")
            print(pane()[-1500:])
            return 1
        time.sleep(4)
        send_line(SEED)
        if not wait_for(lambda: pane().count("READY") >= 2, SEED_TIMEOUT_S, "seed answer"):
            print("FAIL: no seed answer")
            print(pane()[-1500:])
            return 1
        print("PASS: labeled throwaway is live on a real TTY")

        new = sorted(socks() - before)
        new = [p for p in new if p.name != Path(REPLY_SOCK).name]
        print(f"new sockets since boot: {[p.name for p in new]}")
        if len(new) != 1:
            print(f"ABORT: expected exactly 1 new socket, saw {len(new)} — sending nothing.")
            report["abort"] = f"ambiguous socket set: {[p.name for p in new]}"
            return 1
        target = new[0]
        ok, why = verify_ours(target)
        print(f"ownership check: {why}")
        if not ok or not is_live(str(target)):
            print("ABORT: refusing to inject into a socket I cannot prove is mine.")
            report["abort"] = why
            return 1
        report["target_socket"] = str(target)
        report["default_socket_bound_without_flag"] = True

        # Q1: unauthenticated. Q2: same turn, auth frame first.
        report["no_auth"] = attempt(str(target), "no-auth", None, debug_log)
        if report["no_auth"]["reached_model"]:
            print("\n=> unauthenticated injection REACHED the model; auth line not required.")
        else:
            report["with_auth"] = attempt(str(target), "with-auth", token, debug_log)

        (OUT / "spike78-auth-report.json").write_text(json.dumps(report, indent=2))
        (OUT / "spike78-auth-pane.txt").write_text(pane())
        print("\nVERDICT: no-auth reached model = "
              f"{report['no_auth']['reached_model']}"
              + (
                  f"; with-auth reached model = {report['with_auth']['reached_model']}"
                  if "with_auth" in report
                  else ""
              ))
        return 0
    finally:
        listener.close()
        os.environ.pop("CLAUDE_CODE_MESSAGING_TOKEN", None)


if __name__ == "__main__":
    raise SystemExit(main())
