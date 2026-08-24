#!/usr/bin/env python3
"""Replay 7 days of Claude transcripts: live-vs-fixture Stop-gate telemetry.

Counts, per Stop hook: total invocations (live), and blocking decisions.
Also captures per-session identity so per-session state scoping can be checked.
Writes out/stop-gate-telemetry.jsonl (one row per hook per day) and prints a summary.
"""
import json
import os
import sys
import glob
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~/.claude/projects")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(days=7)

invocations = Counter()          # hook command -> count of Stop events where it ran
blocks = Counter()               # hook command -> count where it produced a block decision
sessions_with_block = defaultdict(set)  # hook -> session ids
total_stops = 0
files = glob.glob(os.path.join(HOME, "**", "*.jsonl"), recursive=True)

for path in files:
    try:
        if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < CUTOFF:
            continue
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                if "stop_hook_summary" not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("subtype") != "stop_hook_summary":
                    continue
                ts = rec.get("timestamp", "")
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    when = NOW
                if when < CUTOFF:
                    continue
                total_stops += 1
                sid = rec.get("sessionId", "?")
                infos = rec.get("hookInfos") or []
                errors = set()
                for e in rec.get("hookErrors") or []:
                    errors.add(e.split("]:")[0].strip("["))
                for info in infos:
                    cmd = info.get("command", "?")
                    # normalize to the script basename + args
                    parts = cmd.split()
                    script = parts[-1] if parts else cmd
                    if len(parts) > 2 and "run-record-gate" in cmd:
                        script = "run-record-gate:" + parts[-1]
                    key = script
                    invocations[key] += 1
                    tag = f"{key}"
    except Exception as exc:
        print(f"warn: {path}: {exc}", file=sys.stderr)

print(json.dumps({"total_stop_events": total_stops, "hooks": dict(invocations.most_common())}, indent=2))
