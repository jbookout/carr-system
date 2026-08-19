#!/usr/bin/env python3
"""LIVE test #2: does the held peer turn survive the APPROVAL path?

Receipt 1 proved an outside process can put a turn into a live Claude Code
session's inbound queue, and that the turn is HELD. It could not prove the
next step, because its target was headless (`-p`) and a headless session has
no approval surface: every held message just expired.

This test removes that excuse. The target is a REAL INTERACTIVE session on a
REAL TTY, inside tmux, labeled `spike78-approve` so a human reading either
`tmux ls` or `/tmp/cc-socks/` sees immediately that it is the spike and not
one of Joe's sessions.

PROOF DESIGN (unchanged from receipt 1, and non-negotiable):
The success token is a NONCE generated at runtime and placed ONLY inside the
injected payload. The seed prompt typed into the TTY never contains it. If
the nonce comes out of the model, the socket is the only path it could have
travelled.

Run:  python3 spikes/partner-line-78/test_approve_live.py [--permission-mode M]
Exit 0 = the injected turn reached the model. Exit 1 = it did not, with the
exact state (held / denied / expired) and the recipient's own log lines.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from inject import ReplyListener, inject_keepalive, is_live, peer_turn  # noqa: E402

OUT = Path("/Users/booko/carr-system/out")
SOCK_DIR = "/tmp/cc-socks"
SESSION = "spike78-approve"
TARGET_SOCK = f"{SOCK_DIR}/spike78-approve.sock"
REPLY_SOCK = f"{SOCK_DIR}/spike78-reply.sock"
# A throwaway cwd: not the canonical checkout, not the worktree. Nothing this
# session does can land in either.
CWD = Path("/tmp/spike78-cwd")

BOOT_TIMEOUT_S = 120
SEED_TIMEOUT_S = 120
HOLD_TIMEOUT_S = 90  # the instruction: 90s, then report held
ANSWER_TIMEOUT_S = 120

SEED = (
    "You are a labeled throwaway session for a messaging spike. "
    "Reply with exactly READY and nothing else."
)
# Markers the recipient's own UI uses for the held-peer-message prompt.
# "Another Claude session sent a message" is deliberately NOT here: that string
# is the DELIVERY wrapper the model is shown, so matching on it reads a
# successful delivery as an approval prompt. Run 1 of this test did exactly
# that and pressed approve at a prompt that was never on screen.
PROMPT_MARKERS = (
    "Held message from another session",
    "Deliver this message to Claude",
)
# Where the throwaway's own transcript lands (/tmp is a symlink to /private/tmp).
TRANSCRIPT_DIRS = (
    Path.home() / ".claude/projects/-private-tmp-spike78-cwd",
    Path.home() / ".claude/projects/-tmp-spike78-cwd",
)
TRUST_MARKERS = ("Do you trust the files", "trust the files in this folder")


def tmux(*args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["/opt/homebrew/bin/tmux", *args], capture_output=True, text=True
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def pane() -> str:
    """-J joins wrapped lines so a nonce split by the pane width still matches."""
    return tmux("capture-pane", "-p", "-J", "-t", SESSION, check=False)


def kill_session() -> None:
    tmux("kill-session", "-t", SESSION, check=False)


def wait_for(pred, timeout: float, what: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.5)
    print(f"  timeout waiting for {what} after {timeout}s", file=sys.stderr)
    return False


def mode_footer() -> str:
    """The TUI's own statement of which permission mode it is running in.

    Run 1 of this test recorded `permission_mode: (default)` from its own argv
    and never asked the session. The pane said `auto mode on`. Those are not
    the same claim, so from here the receipt quotes the session, not the flag.
    """
    for line in pane().splitlines():
        if "mode on" in line or "shift+tab to cycle" in line:
            return line.strip()
    return ""


def start_target(debug_log: Path, permission_mode: str | None) -> None:
    kill_session()
    Path(TARGET_SOCK).unlink(missing_ok=True)
    CWD.mkdir(exist_ok=True)
    debug_log.write_bytes(b"")

    cmd = [
        "claude",
        # No -p. A real TTY, which is the entire point of this rerun.
        "--messaging-socket-path", TARGET_SOCK,
        # safe-mode keeps this throwaway off the repo's hooks and MCP servers,
        # so nothing here can touch CARR state. Permissions still work normally,
        # which is what the approval surface depends on.
        "--safe-mode",
        "--debug-to-stderr",
    ]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    # stderr -> file keeps the [cross-session-inbound] lines readable without
    # garbling the TUI, which stays on the pane's tty.
    shell = " ".join(cmd) + f" 2>{debug_log}"

    tmux(
        "new-session", "-d", "-s", SESSION,
        "-x", "200", "-y", "50",
        "-c", str(CWD),
        shell,
    )


def clear_dialogs() -> None:
    """Accept the workspace-trust dialog if this cwd has never been trusted."""
    for _ in range(20):
        text = pane()
        if any(m in text for m in TRUST_MARKERS):
            print("  trust dialog present -> accepting (throwaway /tmp cwd)")
            tmux("send-keys", "-t", SESSION, "Enter")
            time.sleep(2)
            continue
        return


def wait_for_prompt_box() -> bool:
    """The TUI discards keystrokes typed before its input box exists."""
    return wait_for(
        lambda: "shift+tab to cycle" in pane() or "❯" in pane(),
        BOOT_TIMEOUT_S,
        "the TUI input box",
    )


def send_line(text: str) -> None:
    """Type into the pane, and confirm the characters actually landed."""
    probe = text[:24]
    for attempt in range(4):
        tmux("send-keys", "-t", SESSION, "-l", text)
        time.sleep(1.5)
        if probe in pane():
            break
        print(f"  keystrokes did not land (attempt {attempt + 1}); retrying")
        time.sleep(2)
    tmux("send-keys", "-t", SESSION, "Enter")


def approve() -> str:
    """Press the recipient's approve control. Returns what was pressed."""
    # The held-message prompt is a select list whose first option is approve
    # ("Deliver this message to Claude"). Enter takes the highlighted option.
    tmux("send-keys", "-t", SESSION, "Enter")
    time.sleep(4)
    if not any(m in pane() for m in PROMPT_MARKERS):
        return "Enter"
    # Still up: try the numeric selector before giving up.
    tmux("send-keys", "-t", SESSION, "-l", "1")
    time.sleep(3)
    return "Enter, then 1"


def transcript_turns() -> list[tuple[str, str]]:
    """(role, text) from the throwaway's own session log."""
    turns: list[tuple[str, str]] = []
    for d in TRANSCRIPT_DIRS:
        for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
            for line in f.read_text(errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    text = " ".join(
                        b.get("text", "") for b in content if isinstance(b, dict)
                    )
                else:
                    text = content if isinstance(content, str) else ""
                if text.strip():
                    turns.append((rec.get("type", "?"), text))
    return turns


def assistant_said(nonce: str) -> bool:
    """THE proof. A nonce visible on the pane proves nothing — the pane also
    renders the injected turn itself. Only the MODEL's own output counts."""
    return any(role == "assistant" and nonce in text for role, text in transcript_turns())


def delivered_wrapper(nonce: str) -> str:
    for role, text in transcript_turns():
        if role == "user" and nonce in text:
            return text
    return ""


def race_outcome(nonce: str, timeout: float) -> tuple[str, str]:
    """Wait for whichever happens first: delivery, or an approval prompt.

    Returns (outcome, pane_at_that_moment) where outcome is one of
    `delivered` / `prompt` / `timeout`. The pane snapshot is taken INSIDE the
    poll loop, at the instant the condition matched — run 1 matched a prompt
    marker in one capture and then saved a second, later capture that no longer
    contained it, which is why its `approval_prompt_shown: true` could not be
    substantiated afterwards.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if assistant_said(nonce):
            return "delivered", pane()
        snap = pane()
        if any(m in snap for m in PROMPT_MARKERS):
            return "prompt", snap
        time.sleep(0.5)
    return "timeout", pane()


def classify(debug: list[str]) -> dict:
    """The recipient's own verdict, read out of its own log rather than guessed."""
    joined = "\n".join(debug)
    return {
        "routed_to_queue": "Routed user message to queue" in joined,
        "held": "held inbound peer message" in joined,
        "expired": "expired" in joined,
        "denied": "denial receipt" in joined,
        "peer_cred_failed": "peer pid unavailable" in joined,
        "mode_getter_unwired": "permission-mode getter not wired" in joined,
    }


def dbg_lines(debug_log: Path, pattern: str = "cross-session-inbound|uds-messaging|peer-cred") -> list[str]:
    try:
        text = debug_log.read_text(errors="replace")
    except FileNotFoundError:
        return []
    rx = re.compile(pattern)
    return [l.strip() for l in text.splitlines() if rx.search(l)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--permission-mode", default=None)
    ap.add_argument("--label", default="default-mode")
    a = ap.parse_args()

    debug_log = OUT / f"spike78-approve-debug-{a.label}.log"
    pane_log = OUT / f"spike78-approve-pane-{a.label}.txt"
    report = {"label": a.label, "permission_mode": a.permission_mode or "(default)"}

    nonce = "SPIKE78-" + uuid.uuid4().hex[:12].upper()
    listener = ReplyListener(REPLY_SOCK).start()
    conn = None
    print(f"reply address bound: {REPLY_SOCK}")

    try:
        start_target(debug_log, a.permission_mode)
        if not wait_for(lambda: is_live(TARGET_SOCK), 20, "socket bind (first look)"):
            clear_dialogs()
        if not wait_for(lambda: is_live(TARGET_SOCK), BOOT_TIMEOUT_S, "socket bind"):
            print("FAIL: interactive target never bound " + TARGET_SOCK)
            print(pane()[-2000:])
            return 1
        print(f"PASS: labeled INTERACTIVE target bound {TARGET_SOCK}")
        clear_dialogs()
        wait_for_prompt_box()
        time.sleep(4)  # let the startup notices settle before typing

        # Make it a live session with a real conversation. The seed cannot
        # answer the nonce challenge: it has never seen a nonce.
        send_line(SEED)
        # The pane echoes the typed prompt too, and the prompt contains the
        # word READY — so one occurrence proves nothing. Two does.
        if not wait_for(lambda: pane().count("READY") >= 2, SEED_TIMEOUT_S, "seed answer"):
            print("FAIL: target never answered the seed on its TTY")
            print(pane()[-2000:])
            return 1
        print("PASS: target is live and answering on a real TTY")
        time.sleep(3)

        payload = peer_turn(
            f"Partner-line spike 78 approve path. Reply now with exactly {nonce} "
            "and nothing else.",
            reply_to=REPLY_SOCK,
        )
        msg_id = payload["origin"]["msg_id"]
        report["nonce"] = nonce
        report["payload"] = payload
        print("\nINJECT -> " + json.dumps(payload))
        conn = inject_keepalive(TARGET_SOCK, payload)  # stays open across the wait

        # 1) What does the sender hear back on its own bound reply address?
        first = listener.wait_for_status(msg_id, 20)
        report["first_status"] = first
        print("  status frame: " + (json.dumps(first) if first else "(none within 20s)"))

        # 2) Whichever comes first: the model answers, or a prompt asks a human.
        outcome, snap = race_outcome(nonce, HOLD_TIMEOUT_S)
        report["outcome_first"] = outcome
        report["approval_prompt_shown"] = outcome == "prompt"
        sections = [f"=== pane at outcome={outcome} ({a.label}) ===\n{snap}\n"]
        print(f"  first outcome within {HOLD_TIMEOUT_S}s: {outcome}")

        if outcome == "prompt":
            print("  approval prompt IS on the pane; captured to " + str(pane_log))
            pressed = approve()
            report["approved_with"] = pressed
            print(f"  pressed: {pressed}")
            ok = wait_for(
                lambda: assistant_said(nonce),
                ANSWER_TIMEOUT_S,
                f"the model to utter {nonce}",
            )
            report["verdict"] = (
                "delivered after human approval" if ok else "approved but no model output"
            )
            sections.append(f"=== pane after approval ({a.label}) ===\n{pane()}\n")
        elif outcome == "delivered":
            # No prompt was ever shown. Say that plainly; it is the finding.
            report["verdict"] = "delivered WITHOUT any approval prompt"
        else:
            print(f"  neither delivery nor prompt within {HOLD_TIMEOUT_S}s -> HELD")
            report["verdict"] = "held (no prompt surfaced, no delivery)"

        # THE proof, always recomputed from the recipient's own transcript:
        # the pane also renders the injected turn, so a nonce on the pane proves
        # nothing. Only the assistant role uttering it does.
        report["nonce_uttered"] = assistant_said(nonce)
        report["nonce_on_pane"] = nonce in pane()
        report["delivered_wrapper"] = delivered_wrapper(nonce)
        report["mode_footer"] = mode_footer()
        pane_log.write_text("\n".join(sections))

        report["status_frames"] = listener.statuses()
        report["debug"] = dbg_lines(debug_log)
        report["recipient_says"] = classify(report["debug"])
        print("\n--- recipient's own log ---")
        for l in report["debug"][-25:]:
            print("  " + l)
        print("\n--- status frames received on " + REPLY_SOCK + " ---")
        for f in report["status_frames"] or [{}]:
            print("  " + (json.dumps(f) if f else "(none)"))

        (OUT / f"spike78-approve-report-{a.label}.json").write_text(
            json.dumps(report, indent=2)
        )
        print(f"\nVERDICT [{a.label}]: {report.get('verdict')} "
              f"(nonce uttered = {report.get('nonce_uttered')})")
        return 0 if report.get("nonce_uttered") else 1
    finally:
        if conn is not None:
            conn.close()
        listener.close()


if __name__ == "__main__":
    raise SystemExit(main())
