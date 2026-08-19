#!/usr/bin/env python3
"""LIVE test: can a non-Claude-Code process drop a turn onto a live session?

Rule 97326357 — a claim about what a surface can do needs a live test from
that surface. So this does not assert against a mock. It spins up a REAL,
LABELED throwaway Claude Code session (never one of Joe's production
sessions), injects one turn over that session's unix socket from a plain
python process, and asserts the session actually answered the injected turn.

PROOF DESIGN (v2 — v1 had a false positive):
The seed prompt sent over stdin must NOT be able to produce the success
token on its own, or the seed's own answer races the injection and the test
passes for the wrong reason. So the token is a NONCE generated at runtime
and placed ONLY inside the injected payload. The seed session has never seen
it. If the nonce comes back, the session read the socket. Nothing else
explains it.

Run:  python3 spikes/partner-line-78/test_live_inject.py
Exit 0 = injection proven. Exit 1 = not proven, with the evidence printed.
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
from inject import inject, is_live, peer_turn  # noqa: E402

HERE = Path(__file__).parent
OUT = Path(__file__).resolve().parents[2] / "out"
# Labeled on purpose: a human reading /tmp/cc-socks/ must see this is the spike.
TARGET_SOCK = "/tmp/cc-socks/spike78-target.sock"
BOOT_TIMEOUT_S = 90
ANSWER_TIMEOUT_S = 150

# The seed cannot answer the nonce challenge: it never receives a nonce.
SEED = (
    "You are the labeled target session for the Idea 78 partner-line spike. "
    "Reply with exactly READY and nothing else. After that, if and only if you "
    "receive another turn containing a token, reply with exactly that token and "
    "nothing else."
)


def start_target(log_path: Path, debug_path: Path) -> subprocess.Popen:
    """Boot a labeled throwaway session that binds a known socket and waits."""
    try:
        os.unlink(TARGET_SOCK)
    except FileNotFoundError:
        pass
    cmd = [
        "claude", "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        # Explicit path forces UDS messaging on even in print mode, and keeps
        # us off every other socket in the directory.
        "--messaging-socket-path", TARGET_SOCK,
        "--allowedTools", "",
        "--permission-mode", "bypassPermissions",
        # Surfaces the [uds-messaging] lines: connect, parse, hold, deny.
        "--debug-to-stderr",
    ]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=log_path.open("wb"),
        stderr=debug_path.open("wb"),
        cwd=str(HERE),
    )


def send_stdin(proc: subprocess.Popen, text: str) -> None:
    """Seed the session over its own stdin (the sanctioned path)."""
    line = json.dumps(
        {"type": "user", "message": {"role": "user", "content": text}}
    ) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()


def wait_for(pred, timeout: float, what: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.5)
    print(f"  timeout waiting for {what} after {timeout}s", file=sys.stderr)
    return False


def text_of(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except FileNotFoundError:
        return ""


def attempt(proc, log_path, debug_path, label: str, use_peer: bool) -> bool:
    """Inject one nonce-carrying turn and see whether the session answers it."""
    nonce = "SPIKE78-" + uuid.uuid4().hex[:12].upper()
    payload = peer_turn(
        f"Partner-line spike 78. Reply now with exactly {nonce} and nothing else.",
        reply_to=TARGET_SOCK if use_peer else None,
    )
    mark_log = len(text_of(log_path))
    mark_dbg = len(text_of(debug_path))

    print(f"\n--- attempt: {label}")
    print("  INJECT -> " + json.dumps(payload))
    try:
        inject(TARGET_SOCK, payload)
    except OSError as e:
        print(f"  REFUSED at the socket: {e!r}", file=sys.stderr)
        return False

    ok = wait_for(
        lambda: nonce in text_of(log_path)[mark_log:],
        ANSWER_TIMEOUT_S,
        f"the session to echo {nonce}",
    )
    new_dbg = text_of(debug_path)[mark_dbg:]
    uds = [l for l in new_dbg.splitlines() if "uds-messaging" in l]
    print("  [uds-messaging] lines after inject:")
    for l in uds or ["    (none)"]:
        print("    " + l.strip())
    if not ok:
        tail = text_of(log_path)[mark_log:]
        print("  new stdout after inject: " + (tail[-1500:] or "(nothing)"))
    print(f"  => {label}: {'DELIVERED' if ok else 'NOT DELIVERED'}")
    return ok


def main() -> int:
    OUT.mkdir(exist_ok=True)
    log_path = OUT / "spike78-target.jsonl"
    debug_path = OUT / "spike78-target-debug.log"
    log_path.write_bytes(b"")
    debug_path.write_bytes(b"")

    proc = start_target(log_path, debug_path)
    try:
        if not wait_for(lambda: is_live(TARGET_SOCK), BOOT_TIMEOUT_S, "socket bind"):
            print("FAIL: target never bound " + TARGET_SOCK, file=sys.stderr)
            print(text_of(debug_path)[-3000:], file=sys.stderr)
            return 1
        print(f"PASS: labeled target bound {TARGET_SOCK} (pid {proc.pid})")

        # Make it a genuinely LIVE session with a conversation, not a cold
        # process parked on a socket. Wait for its seed answer to LAND before
        # injecting, so no seed output can bleed into the injection window.
        send_stdin(proc, SEED)
        if not wait_for(
            lambda: '"type":"result"' in text_of(log_path)
            or "READY" in text_of(log_path),
            BOOT_TIMEOUT_S,
            "seed answer",
        ):
            print("FAIL: target never answered the seed", file=sys.stderr)
            print(text_of(debug_path)[-3000:], file=sys.stderr)
            return 1
        time.sleep(3)  # let the seed turn fully drain
        print("PASS: target is live and answering on stdin")

        # THE SPIKE. A plain python process — not the Claude Code UI, not this
        # session's stdin — writes one turn to the socket. Try the exact form
        # the binary documents (plain), then the peer-origin form.
        plain = attempt(proc, log_path, debug_path, "plain (as binary documents)", False)
        peer = attempt(proc, log_path, debug_path, "peer origin", True)

        print()
        if plain or peer:
            print("RESULT: INJECTION POSSIBLE "
                  f"(plain={'yes' if plain else 'no'}, peer={'yes' if peer else 'no'})")
            print("evidence: " + str(log_path) + " , " + str(debug_path))
            return 0
        print("RESULT: INJECTION NOT PROVEN from this surface — "
              "the socket accepted the bytes but no turn reached the session.")
        print("evidence: " + str(log_path) + " , " + str(debug_path))
        return 1
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
