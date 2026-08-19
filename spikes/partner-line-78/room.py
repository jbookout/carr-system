#!/usr/bin/env python3
"""Partner room: an append-only turn log two brains write and a human watches.

The acceptance bar for Idea 78 is that a human can open the transcript WHILE
the two sessions talk and read raw turns — who, when, exact text — not a
Claude recap. So the room is the source of truth and the renderer never
summarises: it prints what was written, in order.

This is the LOCAL room. The schema is deliberately the shape a Neon table
takes, so the next slice swaps the storage without touching the renderer or
the injector:

    room_id     text    which conversation ('partner-line')
    seq         bigint  monotonic per room
    at          timestamptz
    speaker     text    'joe-claude' | 'dell-claude' | 'grok' | 'sol' | human
    kind        text    'turn' | 'system' | 'receipt'
    text        text    raw, unsummarised
    msg_id      uuid

Council turns (Grok / Sol) are the same rows with a different speaker, which
is why their visibility costs nothing extra once this exists.

Usage:
  room.py say  <room> <speaker> <text>     append one turn
  room.py show <room>                      print the transcript and exit
  room.py watch <room>                     follow it live (this is the spectator view)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOMS = Path(
    os.environ.get(
        "PARTNER_ROOM_DIR",
        str(Path(__file__).resolve().parents[2] / "out" / "partner-rooms"),
    )
)


def room_path(room: str) -> Path:
    ROOMS.mkdir(parents=True, exist_ok=True)
    return ROOMS / f"{room}.ndjson"


def say(room: str, speaker: str, text: str, kind: str = "turn") -> dict:
    """Append one turn. Append-only and line-atomic so a live reader never
    sees a half-written row."""
    p = room_path(room)
    seq = sum(1 for _ in p.open()) + 1 if p.exists() else 1
    row = {
        "room_id": room,
        "seq": seq,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "speaker": speaker,
        "kind": kind,
        "text": text,
        "msg_id": str(uuid.uuid4()),
    }
    with p.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return row


def render(row: dict) -> str:
    """Raw turn, never a summary: who, when, exact text."""
    at = row.get("at", "")[11:19]
    speaker = row.get("speaker", "?")
    tag = "" if row.get("kind") == "turn" else f" [{row.get('kind')}]"
    body = row.get("text", "")
    head = f"{at}  {speaker}{tag}"
    indent = "\n" + " " * 10
    return head + indent + body.replace("\n", indent)


def read_rows(room: str):
    p = room_path(room)
    if not p.exists():
        return
    with p.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def watch(room: str, from_start: bool = True) -> int:
    """The spectator view. Follows the room and prints turns as they land."""
    p = room_path(room)
    p.touch()
    print(f"# partner room '{room}' — live. ctrl-c to leave.")
    print(f"# {p}\n")
    pos = 0
    if from_start:
        for row in read_rows(room):
            print(render(row) + "\n")
        pos = p.stat().st_size
    else:
        pos = p.stat().st_size
    try:
        while True:
            size = p.stat().st_size
            if size > pos:
                with p.open() as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                for line in chunk.splitlines():
                    if line.strip():
                        try:
                            print(render(json.loads(line)) + "\n", flush=True)
                        except json.JSONDecodeError:
                            pass
            time.sleep(0.4)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("say")
    s.add_argument("room")
    s.add_argument("speaker")
    s.add_argument("text")
    s.add_argument("--kind", default="turn")
    for name in ("show", "watch"):
        q = sub.add_parser(name)
        q.add_argument("room")
    a = p.parse_args(argv)

    if a.cmd == "say":
        print(json.dumps(say(a.room, a.speaker, a.text, a.kind)))
        return 0
    if a.cmd == "show":
        for row in read_rows(a.room):
            print(render(row) + "\n")
        return 0
    return watch(a.room)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
