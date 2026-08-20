#!/usr/bin/env python3
"""watch.py — the per-Mac partner-line watcher (open idea #78, item 1).

WHAT THIS IS. The partner room (mcp-server/src/partner-room.js) already gives
two live verbs behind the deployed Worker: add-room-turn writes one raw turn,
read-room polls them back oldest-first. Both Macs already write into it. What
does not exist yet is anything that watches the room and puts the OTHER
partner's turns in front of the human sitting at THIS Mac — the room is a
transcript, not an interrupt, until something reads it and acts.

WHY A POLLER, NOT A PUSH. The spike (spikes/partner-line-78) proved a live
Claude Code session accepts an injected turn over its own unix socket; nothing
on the Worker side can reach into that socket from another machine, so a local
process has to bridge the two. This script is that bridge: poll read-room,
find turns the OTHER partner authored, drop them into a live local session.

SYMMETRIC BY DESIGN, same stance as pipelines/partner_ping.py. One copy in
the repo, both partners run it, and it watches whichever partner is NOT the
one running it. self-resolution below is copied from partner_ping's
resolve_self() almost verbatim, for the same reason stated there: a wrong
guess here silently watches the wrong partner and never fires, which is the
worst possible failure.

CONSENT POSTURE — decision 351b9995: incoming turns AUTO-INJECT, but VISIBLY
and ABORTABLY.
  * VISIBLE: every injection fires a macOS notification (notify(), copied from
    partner_ping's own osascript pattern) naming who the turn is from. There
    is no code path that injects silently.
  * ABORTABLE: a kill switch, ~/.config/carr/partner-line-paused. While that
    file exists, the watcher keeps polling and keeps advancing its watermark
    (so nothing queues up and dumps on the human once the file is removed) —
    it just injects nothing, and says so on every turn it would have sent.
    Removing the file resumes.

INJECTION SAFETY — the one rule that is non-negotiable, not just a default:
this script NEVER writes to a pid-named socket (a Claude Code session's
default self-bound socket, /tmp/cc-socks/<pid>.sock). It only writes to a
REGISTERED LABEL's socket, resolved from --target or the
~/.config/carr/partner-line-target file. socket_path_for_label() below is the
one place a label becomes a filesystem path, and it refuses anything shaped
like a bare pid before it ever builds the path — there is no argument that
reaches an unlabeled socket. See that function's docstring for the exact
check.

READ PATH. This never touches the database directly (decision already made
for the room itself — see partner-room.js's header). It shells out to
tools/call-verb.py, exactly the documented `./run.sh call read-room '{...}'`
path, so every read goes through the deployed Worker's identity derivation
like any other caller.

RUN IT:
    .venv/bin/python tools/partner-line/watch.py --target dell-main
        Long-running: polls every --interval seconds (default 5) until
        interrupted.
    .venv/bin/python tools/partner-line/watch.py --target dell-main --once
        One poll, then exit. What the tests and a cron-style call use.
    --dry-run    print what WOULD be injected, inject nothing, never advance
                 the stored watermark.
    --since SEQ  read starting after this seq instead of the stored one, for
                 this run only; the run's own progress is still persisted
                 unless --dry-run is also given (mirrors partner_ping.py).

See tools/partner-line/README.md for install/config and the current honest
limits (no live end-to-end test against a real session yet — that is a
stated follow-up, not an oversight).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CALL_VERB = REPO / "tools" / "call-verb.py"

CONFIG_DIR = Path.home() / ".config" / "carr"
STATE_PATH = CONFIG_DIR / "partner-line-watch.json"
WHOAMI_PATH = CONFIG_DIR / "partner"
TARGET_PATH = CONFIG_DIR / "partner-line-target"
PAUSE_PATH = CONFIG_DIR / "partner-line-paused"

DEFAULT_SOCK_DIR = "/tmp/cc-socks"
DEFAULT_ROOM = "partner-line"
DEFAULT_INTERVAL = 5.0

# SYMMETRIC BY DESIGN — see pipelines/partner_ping.py's identical constant and
# the comment above it. Not Joe-shaped or Dell-shaped on purpose.
PARTNERS = ("joe", "dell")
DISPLAY = {"joe": "Joe", "dell": "Dell"}

TURN_KIND = "turn"

# A pid-named socket is Claude Code's own default self-bound address — the
# filename Claude Code chooses when nothing registers a label for it. Bare
# digits, nothing else.
PID_SOCK_RE = re.compile(r"^\d+\.sock$")
# A registered label is a plain slug: this is deliberately closer to the
# repo's other slug conventions (mcp-server/src/partner-room.js's own room/seat
# SLUG) than to anything socket-specific — it exists mainly to keep a label
# from smuggling a path separator into sock_dir, not to be clever.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# ---------------------------------------------------------------------------
# Self / target resolution — refuse to guess, same stance as partner_ping.py.
# ---------------------------------------------------------------------------

def resolve_self(whoami_path: Path = WHOAMI_PATH) -> str:
    """Who is running this? Env var wins, then ~/.config/carr/partner.

    Copied stance from pipelines/partner_ping.py's resolve_self(): a wrong
    guess here produces a watcher that watches the wrong partner and silently
    never fires, indistinguishable from a quiet week. Refusing beats guessing.
    """
    who = (os.environ.get("CARR_PARTNER") or "").strip().lower()
    if not who:
        try:
            who = whoami_path.read_text().strip().lower()
        except FileNotFoundError:
            who = ""
    if who not in PARTNERS:
        sys.exit(
            f"who is this? set CARR_PARTNER to one of {PARTNERS}, or write it "
            f"into {whoami_path}. Refusing to guess — a wrong guess makes this "
            "watch the wrong partner and never fire."
        )
    return who


def resolve_target_label(cli_target: str | None, target_path: Path = TARGET_PATH) -> str:
    """The Claude Code session label to inject into. Refuses to run without one.

    Deliberately no fallback to "whatever socket happens to exist" — that is
    exactly the pid-socket path this script must never take. See
    socket_path_for_label() for the actual refusal.
    """
    label = (cli_target or "").strip()
    if not label:
        try:
            label = target_path.read_text().strip()
        except FileNotFoundError:
            label = ""
    if not label:
        sys.exit(
            "no target session label — pass --target <label> or write one into "
            f"{target_path}. A labeled target is required; this script never "
            "guesses a socket to write to."
        )
    try:
        return validate_target_label(label)
    except ValueError as e:
        sys.exit(str(e))


def validate_target_label(label: str) -> str:
    """Raise ValueError on anything that is not a plain, non-pid label.

    Two independent refusals:
      1. shape — a label must look like a slug (LABEL_RE), so it cannot carry
         a "/" or otherwise escape sock_dir when joined into a path.
      2. pid-shape — the label (with ".sock" appended if not already present)
         must NOT match PID_SOCK_RE. A Claude Code session with no registered
         label binds its own pid as the socket name; injecting there would be
         writing to whatever session happens to be running, not the one a
         human deliberately labeled. This is the one non-negotiable rule from
         the spec: "There is no argument that reaches an unlabeled/pid socket."
    """
    label = label.strip()
    if not label or not LABEL_RE.match(label):
        raise ValueError(f"invalid target label: {label!r}")
    candidate = label if label.endswith(".sock") else f"{label}.sock"
    if PID_SOCK_RE.match(candidate):
        raise ValueError(
            f"refusing pid-shaped socket target {candidate!r} — only a "
            "registered label may be injected to, never a bare pid"
        )
    return label


def socket_path_for_label(label: str, sock_dir: str = DEFAULT_SOCK_DIR) -> str:
    """The one place a label becomes a filesystem path. Always validates first."""
    label = validate_target_label(label)
    return os.path.join(sock_dir, f"{label}.sock")


# ---------------------------------------------------------------------------
# Watermark state — plain JSON, same shape as partner_ping.py's state file.
# ---------------------------------------------------------------------------

def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Pure turn logic — no socket, no subprocess, no network. This is the part
# the acceptance-bar tests exercise directly.
# ---------------------------------------------------------------------------

def new_turns(turns: list[dict], since_seq: int) -> list[dict]:
    """Turns strictly after since_seq, in the order given.

    An explicit filter on top of the server's own after_seq cursor: if a
    caller ever re-hands the watcher a batch that includes something already
    past the watermark (a --since rewind, a retried fetch), this is the one
    place that guarantees an already-seen seq never gets processed twice.
    """
    return [t for t in turns if int(t.get("seq", 0)) > since_seq]


def classify_turn(turn: dict, self_partner: str) -> str:
    """'inject' | 'skip_self' | 'skip_kind' — never a live decision, just a fact.

    Per spec: inject only when sponsor != self (the OTHER brain, not our own
    echo) AND kind == 'turn' (system/receipt rows are skipped, watermark
    still advances over them).
    """
    if turn.get("kind") != TURN_KIND:
        return "skip_kind"
    if turn.get("sponsor") == self_partner:
        return "skip_self"
    return "inject"


def next_watermark(current: int, turns: list[dict]) -> int:
    """The watermark advances over every turn handed to it — injected or
    skipped alike — because a skip is still successfully accounted for."""
    seqs = [int(t.get("seq", current)) for t in turns]
    return max([current, *seqs])


def format_body(turn: dict) -> str:
    """"[dell · claude] <body>" — so a human watching sees who this is from."""
    sponsor = turn.get("sponsor") or "?"
    seat = turn.get("seat") or "?"
    body = turn.get("body") or ""
    return f"[{sponsor} · {seat}] {body}"


def build_peer_message(text: str) -> dict:
    """The exact NDJSON object the spec requires, nothing added."""
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "origin": {"kind": "peer"},
    }


# ---------------------------------------------------------------------------
# I/O edges — subprocess (read path), socket (inject path), osascript
# (notify). Kept thin and each individually swappable so poll_once() can be
# unit-tested with fakes standing in for all three.
# ---------------------------------------------------------------------------

def fetch_turns(after_seq: int, *, room: str = DEFAULT_ROOM, limit: int = 50,
                 call_verb_path: Path = CALL_VERB) -> dict:
    """The one and only read path: tools/call-verb.py read-room, exactly the
    documented `./run.sh call read-room '{...}'` route — never a direct
    database connection. Raises RuntimeError on any failure."""
    args_json = json.dumps({"room": room, "after_seq": after_seq, "limit": limit})
    proc = subprocess.run(
        [sys.executable, str(call_verb_path), "read-room", args_json],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"read-room failed (rc={proc.returncode}): {proc.stderr.strip()[:2000]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"read-room returned non-JSON stdout: {proc.stdout[:500]!r}"
        ) from e


def inject_to_socket(sock_path: str, payload: dict, timeout: float = 5.0) -> None:
    """Write ONE newline-delimited JSON object to a live session socket.

    Wire format per spec, matching what the spike (spikes/partner-line-78/
    inject.py) proved live against a real Claude Code session.
    """
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        s.sendall(line.encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
    finally:
        s.close()


def notify(title: str, subtitle: str, message: str) -> None:
    """macOS notification via osascript — copied pattern from
    pipelines/partner_ping.py's notify(). The mandatory visible signal for
    decision 351b9995: every injection fires one of these, no exceptions."""
    def esc(s):
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')[:220]

    script = (
        f'display notification "{esc(message)}" '
        f'with title "{esc(title)}" subtitle "{esc(subtitle)}"'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True)


# ---------------------------------------------------------------------------
# One poll cycle. fetch/injector/notifier are injected so this can be unit
# tested with no live socket and no live network — the acceptance bar.
# ---------------------------------------------------------------------------

def poll_once(*, self_partner: str, target_label: str, room: str = DEFAULT_ROOM,
              state_path: Path = STATE_PATH, pause_path: Path = PAUSE_PATH,
              since_override: int | None = None, dry_run: bool = False,
              sock_dir: str = DEFAULT_SOCK_DIR,
              fetch=fetch_turns, injector=inject_to_socket, notifier=notify,
              log=print) -> dict:
    state = load_state(state_path)
    last_seq = since_override if since_override is not None else int(state.get("last_seq", 0))

    result = fetch(last_seq, room=room)
    all_turns = result.get("turns", [])
    turns = new_turns(all_turns, last_seq)

    if not turns:
        log(f"[{self_partner}] nothing new after seq {last_seq}")
        return {"processed": 0, "injected": 0, "watermark": last_seq, "paused": False}

    paused = pause_path.exists()
    sock_path = socket_path_for_label(target_label, sock_dir)

    injected = 0
    for t in turns:
        action = classify_turn(t, self_partner)
        if action != "inject":
            continue
        text = format_body(t)
        if dry_run:
            log(f"WOULD INJECT: {text}")
            continue
        if paused:
            log(f"PAUSED (kill switch at {pause_path}) — not injecting: {text}")
            continue
        payload = build_peer_message(text)
        injector(sock_path, payload)
        notifier(
            f"Partner line — {DISPLAY.get(t.get('sponsor'), t.get('sponsor'))}",
            t.get("seat") or "",
            (t.get("body") or "")[:200],
        )
        injected += 1

    watermark = next_watermark(last_seq, turns)
    if not dry_run:
        save_state(state_path, {"last_seq": watermark})

    log(
        f"[{self_partner}] {len(turns)} turn(s) processed, {injected} injected"
        + (" (dry run, watermark unchanged)" if dry_run else f"; watermark {last_seq} -> {watermark}")
        + (" [PAUSED]" if paused and not dry_run else "")
    )
    return {"processed": len(turns), "injected": injected, "watermark": watermark, "paused": paused}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", help="registered Claude Code session label to inject into "
                                      "(falls back to ~/.config/carr/partner-line-target)")
    ap.add_argument("--room", default=DEFAULT_ROOM, help=f"partner room name (default {DEFAULT_ROOM!r})")
    ap.add_argument("--since", type=int, default=None,
                     help="read starting after this seq instead of the stored watermark, "
                          "for this run's first poll only")
    ap.add_argument("--dry-run", action="store_true",
                     help="print what WOULD be injected; inject nothing; never advance the watermark")
    ap.add_argument("--once", action="store_true", help="poll exactly once, then exit")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                     help=f"seconds between polls in the long-running loop (default {DEFAULT_INTERVAL})")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    self_partner = resolve_self()
    target_label = resolve_target_label(args.target)

    since_override = args.since
    try:
        while True:
            poll_once(
                self_partner=self_partner,
                target_label=target_label,
                room=args.room,
                since_override=since_override,
                dry_run=args.dry_run,
            )
            # --since only steers the FIRST poll of a run; once that poll has
            # (possibly) advanced the real watermark, every later poll in this
            # same run reads from it normally, exactly like partner_ping.py's
            # one-shot --since only ever affecting the run it's given to.
            since_override = None
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
