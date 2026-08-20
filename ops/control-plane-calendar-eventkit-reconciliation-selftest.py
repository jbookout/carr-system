#!/usr/bin/env python3
"""Pin the Control Plane calendar workflow to the governed EventKit source."""
from __future__ import annotations

import json
import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILED.append(label)


manifest = json.loads((ROOT / "ops/config/control-plane-workflows.v1.json").read_text())
workflow = next(item for item in manifest["workflows"] if item["key"] == "calendar-fetch-daily")
execution = workflow["execution"]
check("calendar workflow advances to the EventKit definition", workflow["version"] >= 4)
check("calendar workflow executes the attendee-aware EventKit capture",
      execution["entrypoint"] == "bin/calendar-eventkit-capture.sh")
check("calendar shadow is the exact read-only EventKit path",
      execution["shadow_args"] == ["--dry-run", "--receipt-safe", "--days", "7"])
check("calendar canary has the registered isolated record destination contract",
      execution["canary"]["enabled"] is True
      and execution["canary"].get("isolation_guard") == "calendar-fetch-daily.canary.v1"
      and execution["canary"].get("args") == ["--canary", "--days", "7"])
check("calendar legacy owner is the real launchd surface",
      workflow["legacy_schedule"].get("provider") == "launchd")
check("calendar inventory names EventKit and not a Drive feed",
      "EventKit" in json.dumps(workflow["inventory"])
      and "Drive" not in json.dumps(workflow["inventory"]))

plist = plistlib.loads((ROOT / "ops/launchd/com.carr.calendar-eventkit.plist").read_bytes())
intervals = plist.get("StartCalendarInterval", [])
check("tracked EventKit launchd cadence is Monday through Friday at 07:20",
      [(row.get("Weekday"), row.get("Hour"), row.get("Minute")) for row in intervals]
      == [(2, 7, 20), (3, 7, 20), (4, 7, 20), (5, 7, 20), (6, 7, 20)])

registry = json.loads((ROOT / "ops/config/control-plane-scheduler-cutover.v1.json").read_text())
surfaces = [row for row in registry["surfaces"] if row["workflow_key"] == "calendar-fetch-daily"]
check("calendar has exactly one native legacy surface", len(surfaces) == 1)
if surfaces:
    surface = surfaces[0]
    check("calendar native surface is the EventKit launchd job",
          surface.get("scheduler_kind") == "launchd"
          and surface.get("locator") == "com.carr.calendar-eventkit"
          and surface.get("workflow_version") == workflow["version"]
          and surface.get("repo_plist_relpath") == "ops/launchd/com.carr.calendar-eventkit.plist")

runner = (ROOT / "tools/control-plane.py").read_text()
check("calendar command evidence recognizes EventKit markers",
      "calendar-capture: source=eventkit mode=shadow" in runner
      and "calendar-pull: source=calendar" not in runner
      and 'CARR_CALENDAR_CANARY_DSN' in runner
      and 'if mode == "canary"' in runner)

capture = (ROOT / "bin/calendar-eventkit-capture.sh").read_text()
# Contents/Resources/run.zsh, not Contents/MacOS/. The bundle's zsh logic moved
# there on 2026-08-18 when the main executable became a compiled Mach-O stub:
# macOS 26 refuses to launch a bundle whose executable is a script (-10669), so
# the script cannot live at the MacOS/ path any more. Reading the old path here
# now reads a binary and dies on invalid UTF-8 rather than failing an assertion.
bundle = (ROOT / "tools/CARR Calendar Access.app/Contents/Resources/run.zsh").read_text()
dump = (ROOT / "tools/calendar-attendee-dump.py").read_text()
check("EventKit capture supports an isolated output root",
      "CARR_CALENDAR_OUTPUT_ROOT" in capture
      and "CARR_CALENDAR_OUTPUT_ROOT" in bundle
      and "CARR_CALENDAR_OUTPUT_ROOT" in dump)
check("EventKit canary diverts to its isolated receipt target before live writes",
      "calendar-canary-record.py" in capture and "--canary requires explicit control-plane canary mode" in capture)
check("EventKit capture emits finite shadow and live evidence markers",
      "source=eventkit mode=" in capture and "writes=" in capture and "failed=" in capture
      and "RECEIPT_SAFE" in capture)
check("obsolete published-feed reader is not the registered Control Plane command",
      execution["entrypoint"] != "bin/pull-gmail-calendar.py")

print(f"control-plane calendar EventKit reconciliation — {14-len(FAILED)}/14 passed")
raise SystemExit(1 if FAILED else 0)
