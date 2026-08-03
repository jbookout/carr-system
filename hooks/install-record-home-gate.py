#!/usr/bin/env python3
"""install-record-home-gate.py — merge the record-home gate into ~/.claude/settings.json.

WHY A SCRIPT INSTEAD OF "paste this JSON". Claude cannot edit settings.json
(classifier-blocked, same gate as migrate.py --apply), so the house pattern is
write-it-and-hand-it-over. But handing over a raw JSON fragment invites two
failure modes that both happened or nearly happened on 2026-08-03: pasting it
into a SHELL (zsh: bad pattern) and hand-merging it into the wrong nesting level,
which produces a settings file that silently loads with no hooks at all.

So the handover is a script Joe runs, not a fragment Joe edits.

WHAT IT DOES
  * refuses to run if settings.json is not valid JSON (never write over a file
    we could not parse);
  * is IDEMPOTENT — if a PreToolUse entry already points at record-home-gate.py,
    it reports that and changes nothing;
  * preserves every other key and every other hook, merging only the one entry;
  * writes a timestamped .bak beside the original BEFORE touching it;
  * re-reads and re-parses the result, and restores the backup if the write
    produced anything unparseable.

DRY RUN BY DEFAULT. It prints what it would change and exits. Pass --apply to
write. That is the same shape as migrate.py and salesforce-diff.

    python3 ~/carr-system/hooks/install-record-home-gate.py            # preview
    python3 ~/carr-system/hooks/install-record-home-gate.py --apply    # write

RESTART THE SESSION AFTER APPLYING. Hooks are read at session start.
"""

import datetime
import json
import os
import shutil
import sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
GATE = "/Users/booko/carr-system/hooks/record-home-gate.py"
ENTRY = {
    "matcher": "Write|Edit|MultiEdit",
    "hooks": [{"type": "command",
               "command": f"/usr/bin/env python3 {GATE}",
               "timeout": 10}],
}


def die(msg, code=1):
    print(f"ERROR: {msg}")
    sys.exit(code)


def main():
    apply = "--apply" in sys.argv

    if not os.path.exists(GATE):
        die(f"the gate script is missing at {GATE}")
    if not os.path.exists(SETTINGS):
        die(f"no settings file at {SETTINGS}")

    raw = open(SETTINGS).read()
    try:
        cfg = json.loads(raw)
    except Exception as exc:
        die(f"{SETTINGS} is not valid JSON ({exc}) — not touching it. Fix it by hand first.")

    hooks = cfg.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])

    for e in pre:
        for h in e.get("hooks", []):
            if "record-home-gate.py" in str(h.get("command", "")):
                print("Already installed — a PreToolUse entry already points at "
                      "record-home-gate.py. Nothing to do.")
                print(f"  matcher: {e.get('matcher')}")
                sys.exit(0)

    pre.append(ENTRY)

    print(f"settings file : {SETTINGS}")
    print(f"PreToolUse entries: {len(pre) - 1} -> {len(pre)}")
    print("adding:")
    print(json.dumps(ENTRY, indent=2))
    existing = [e.get("matcher", "(no matcher)") for e in pre[:-1]]
    print(f"preserved PreToolUse matchers: {existing}")
    print(f"preserved top-level keys: {sorted(k for k in cfg if k != 'hooks')}")
    other = sorted(k for k in hooks if k != "PreToolUse")
    print(f"preserved hook events: {other}")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to install.")
        sys.exit(0)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{SETTINGS}.bak-{stamp}"
    shutil.copy2(SETTINGS, backup)
    print(f"\nbackup: {backup}")

    with open(SETTINGS, "w") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")

    try:
        json.loads(open(SETTINGS).read())
    except Exception as exc:
        shutil.copy2(backup, SETTINGS)
        die(f"the write produced unparseable JSON ({exc}) — restored from {backup}")

    print("WROTE OK and re-parsed clean.")
    print("\nRESTART THE SESSION — hooks are read at session start.")
    print("Then verify with:")
    print("  cd ~/carr-system && .venv/bin/python tools/test-record-home-gate.py")


if __name__ == "__main__":
    main()
