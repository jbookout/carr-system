#!/usr/bin/env python3
"""Hermetic side-effect checks for the two isolated deterministic canaries."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CALENDAR = REPO / "bin" / "pull-gmail-calendar.py"
NOTES = REPO / "bin" / "notes-sweep-post.sh"


def main() -> int:
    failed: list[str] = []
    total = 0

    def check(label: str, value: bool) -> None:
        nonlocal total
        total += 1
        print(f"  {'ok  ' if value else 'FAIL'} {label}")
        if not value:
            failed.append(label)

    with tempfile.TemporaryDirectory(prefix="carr-canary-") as raw:
        tmp = Path(raw)
        home = tmp / "home"; home.mkdir()
        root = REPO / "out" / "canary" / f"selftest-{os.getpid()}"
        config = tmp / "calendar-canary.env"; notes_config = tmp / "notes-canary.env"
        base = {"HOME": str(home), "CARR_CONTROL_PLANE_MODE": "canary",
                "CARR_CALENDAR_CANARY_ENV": str(config), "CARR_NOTES_CANARY_ENV": str(notes_config),
                "CARR_CALENDAR_CANARY_ROOT": str(root / "calendar"),
                "CARR_NOTES_CANARY_ROOT": str(root / "notes"),
                "CARR_VAULT": str(tmp / "empty-vault"),
                "CARR_INGEST_URL": "https://live.invalid/ingest"}

        def run_calendar() -> subprocess.CompletedProcess[str]:
            return subprocess.run(["python3", str(CALENDAR), "--canary"], env={**os.environ, **base}, text=True, capture_output=True)

        def run_notes(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["zsh", str(NOTES), "--canary", *args], env={**os.environ, **base}, text=True, capture_output=True)

        def rejected(label: str, writer) -> None:
            before = list(root.rglob("*")) if root.exists() else []
            result = writer()
            after = list(root.rglob("*")) if root.exists() else []
            check(label, result.returncode == 78 and before == after)

        def configs(url: str, destination: str = "canary-destination") -> None:
            config.write_text(f"CARR_CANARY_INGEST_URL={url}\nCARR_CANARY_INGEST_TOKEN_CALENDAR=calendar-token\nCARR_CANARY_DESTINATION_ID={destination}\n")
            notes_config.write_text(f"CARR_CANARY_INGEST_URL={url}\nCARR_CANARY_INGEST_TOKEN_NOTES=notes-token\nCARR_CANARY_DESTINATION_ID={destination}\n")
            config.chmod(0o600); notes_config.chmod(0o600)

        rejected("calendar missing config refuses before isolated writes", run_calendar)
        rejected("Notes missing config refuses before isolated writes", lambda: run_notes("--status"))
        live_log = REPO / "out" / "capture-lanes.log"
        before_live = live_log.read_bytes() if live_log.exists() else None
        gmail = subprocess.run(["python3", str(CALENDAR), "--gmail"], env={**os.environ, **base}, text=True, capture_output=True)
        after_live = live_log.read_bytes() if live_log.exists() else None
        check("calendar canary-mode Gmail boundary refuses before any live write", gmail.returncode == 78 and before_live == after_live)

        configs("https://live.invalid/ingest", "isolated")
        rejected("calendar equal live URL refuses before network/local state", run_calendar)
        rejected("Notes equal live URL refuses before network/local state", lambda: run_notes("--status"))

        config.write_text("CARR_CANARY_INGEST_URL=$(bad)\n"); notes_config.write_text("CARR_CANARY_INGEST_URL=$(bad)\n"); config.chmod(0o600); notes_config.chmod(0o600)
        rejected("calendar malformed config refuses before network/local state", run_calendar)
        rejected("Notes malformed config refuses before network/local state", lambda: run_notes("--status"))

        configs("https://canary.invalid/ingest", "isolated"); config.chmod(0o644); notes_config.chmod(0o644)
        rejected("calendar insecure config refuses before network/local state", run_calendar)
        rejected("Notes insecure config refuses before network/local state", lambda: run_notes("--status"))

        configs("https://canary.invalid/ingest"); config.write_text(config.read_text()+"UNRELATED_TOKEN=x\n"); notes_config.write_text(notes_config.read_text()+"UNRELATED_TOKEN=x\n")
        rejected("calendar unknown config key refuses before writes", run_calendar)
        rejected("Notes unknown config key refuses before writes", lambda: run_notes("--status"))
        configs("https://canary.invalid/ingest"); config.write_text(config.read_text()+"CARR_CANARY_INGEST_URL=https://other.invalid\n"); notes_config.write_text(notes_config.read_text()+"CARR_CANARY_INGEST_URL=https://other.invalid\n")
        rejected("calendar duplicate config key refuses before writes", run_calendar)
        rejected("Notes duplicate config key refuses before writes", lambda: run_notes("--status"))

        configs("https://canary.invalid/ingest?x=1&y=2")
        outside = tmp / "not-isolated"
        hostile_base = {**base, "CARR_CALENDAR_CANARY_ROOT": str(outside), "CARR_NOTES_CANARY_ROOT": str(outside)}
        old_base = base.copy(); base.update(hostile_base)
        rejected("calendar ambient root outside out/canary refuses before writes", run_calendar)
        rejected("Notes ambient root outside out/canary refuses before writes", lambda: run_notes("--status"))
        base.clear(); base.update(old_base)
        root.mkdir(parents=True, exist_ok=True)
        (root / "escape").symlink_to(outside, target_is_directory=True)
        hostile_base = {**base, "CARR_CALENDAR_CANARY_ROOT": str(root / "escape"), "CARR_NOTES_CANARY_ROOT": str(root / "escape")}
        old_base = base.copy(); base.update(hostile_base)
        rejected("calendar symlink parent escape refuses before writes", run_calendar)
        rejected("Notes symlink parent escape refuses before writes", lambda: run_notes("--status"))
        base.clear(); base.update(old_base)
        (root / "escape").unlink(); root.rmdir()
        configs("https://canary.invalid/ingest", "bad destination")
        rejected("calendar unsafe destination refuses before writes", run_calendar)
        rejected("Notes unsafe destination refuses before writes", lambda: run_notes("--status"))
        configs("'https://canary.invalid/ingest?x=1&y=2'")
        calendar = run_calendar()
        check("calendar valid canary has redacted identity marker", calendar.returncode == 0 and "mode=canary destination=canary-destination" in calendar.stdout and "calendar-token" not in calendar.stdout and "https://canary.invalid" not in calendar.stdout)
        check("calendar valid canary creates only isolated log state", (root / "calendar" / "calendar-pull.log").is_file())
        notes = run_notes("--status")
        expected = {root / "notes" / "pending", root / "notes" / "sent", root / "notes" / "failed", root / "notes" / "swept-ids.txt", root / "notes" / "audio-only.txt"}
        check("Notes valid canary status has redacted identity marker", notes.returncode == 0 and "mode=canary destination=canary-destination" in notes.stdout and "notes-token" not in notes.stdout and "https://canary.invalid" not in notes.stdout)
        check("Notes valid canary writes queue and ledger state only under isolated root", all(path.exists() for path in expected))
        captured: list[tuple[str, str, dict]] = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                captured.append((self.path, self.headers.get("authorization", ""), json.loads(body)))
                self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b'{}')
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}/canary"
        try:
            root.mkdir(parents=True, exist_ok=True)
            configs(endpoint)
            vault = tmp / "vault" / "DNA" / "Team"; vault.mkdir(parents=True)
            today = date.today().strftime("%Y%m%d")
            (vault / "calendar-latest.ics").write_text(f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:canary-event\nDTSTART:{today}T120000Z\nDTEND:{today}T130000Z\nSUMMARY:Private Test\nEND:VEVENT\nEND:VCALENDAR\n")
            base["CARR_VAULT"] = str(tmp / "vault")
            calendar = run_calendar()
            check("calendar loopback posts one isolated event with exact auth", bool(calendar.returncode == 0 and len(captured) == 1 and captured[0][0] == "/canary" and captured[0][1] == "Bearer calendar-token" and captured[0][2].get("external_id") and "calendar-token" not in calendar.stdout))
            copied = tmp / "notes-copy.sh"; fake = tmp / "fake-osascript"
            copied_source = NOTES.read_text().replace('REPO="$(cd "$(dirname "$0")/.." && pwd)"', f'REPO="{REPO}"')
            copied.write_text(copied_source.replace("/usr/bin/osascript", str(fake))); copied.chmod(0o700)
            fake.write_text("#!/bin/zsh\nif [ \"$2\" = ids ]; then print note-1; else d=\"$4\"; print -r -- Call > \"$d/1.name\"; print -r -- 2026-01-01 > \"$d/1.created\"; print -r -- 2026-01-01 > \"$d/1.modified\"; print -r -- $'Call\\nTranscript' > \"$d/1.text\"; print -r -- note-1 > \"$d/1.id\"; print 'OK 1'; fi\n"); fake.chmod(0o700)
            result = subprocess.run(["zsh", str(copied), "--canary"], env={**os.environ, **base}, text=True, capture_output=True)
            check("Notes copied harness posts queued isolated payload", bool(result.returncode == 0 and len(captured) == 2 and captured[1][0] == "/canary" and captured[1][1] == "Bearer notes-token" and captured[1][2].get("external_id") == "note-1" and list((root / "notes" / "sent").glob("*.json")) and "failed=0" in result.stdout and "notes-token" not in result.stdout))
        finally:
            server.shutdown(); thread.join(); server.server_close()
        shutil.rmtree(root)

    print(f"control-plane-isolated-canary-selftest — {total-len(failed)}/{total} passed")
    if failed:
        print("FAILED: " + "; ".join(failed)); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
