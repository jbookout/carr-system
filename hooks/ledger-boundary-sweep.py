#!/usr/bin/env python3
"""ledger-boundary-sweep.py — fire the BOUNDARY half of rule bbffc139.

WHY THIS EXISTS, and it is a diagnosis rather than a feature request.

Rule bbffc139 has two halves. The TRIGGER half — "Joe overruled a
recommendation", "Joe stated a prediction" — is already hooked by
ledger-sweep.py on Stop, and it works: on 2026-08-03 it fired eight times in one
session and eight entries got logged.

The BOUNDARY half — "before any commit, before any deploy, before writing a
handoff or checkpoint, re-read the exchange and ask what belongs in a ledger" —
was never hooked at all. It depended on a session remembering, mid-task, to stop
and sweep. On 2026-08-03 that session made EIGHT COMMITS AND ONE PRODUCTION
DEPLOY and swept before none of them, then had to be told by Joe that his
personal observations were missing again. Six of his overrides went unlogged
until he asked.

That is the same trap bbffc139 itself diagnosed for its predecessor: a rule
phrased as a judgment the session must make while doing something else. A commit
is not a judgment. It is a detectable event. So it gets a hook, exactly like the
unattended guard, and stops depending on attention.

WHY IT BLOCKS RATHER THAN ADVISES. The structured advisory contract (exit 0 plus
hookSpecificOutput) is available and was rejected for the same reason
guard-unattended.py rejected it: on any build that does not parse the JSON,
exit 0 reads as ALLOW and the gate fails open SILENTLY. An advisory that can be
ignored is what the ledger already had, and it produced zero entries for weeks.
This blocks ONCE, names what is unlogged, and lets the commit through on the
retry. One extra round trip is the whole cost.

IT ONLY BLOCKS WHEN A SWEEP IS ACTUALLY OWED — when a qualifying human turn
appears in the transcript AFTER the last ledger write. A session that has been
logging as it goes never sees this hook at all. That matters: a gate that fires
on every commit regardless would be trained away within a day.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(os.path.expanduser("~/carr-system"))
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")
STATE = os.path.expanduser("~/carr-system/out/ledger-boundary-state.json")
LEDGER_VERBS = ("log-decision", "teach", "update-decision")

# The boundaries rule bbffc139 names. Deploys and pushes matter as much as
# commits: shipping is the moment the work becomes real and the moment a
# session's attention is furthest from the ledger.
BOUNDARIES = re.compile(
    r"(git\s+commit|git\s+push|wrangler\s+deploy|"
    r"launchctl\s+load|\bhandoff\b|\bcheckpoint\b)", re.I)

# Deliberately BROADER than ledger-sweep.py's triggers. This runs at a boundary,
# not on every turn, so a false positive costs one retry rather than constant
# noise — and the failure this exists to stop is under-logging, not over-logging.
QUALIFYING = re.compile(
    r"(\bactually\b|\bno,?\s|\bi disagree\b|\bthat'?s not\b|\binstead\b|"
    r"\brather than\b|\bi'?m not convinced\b|\bi don'?t think\b|\bwhy (don'?t|not)\b|"
    r"\byou should\b|\bi'?d (say|argue|rather)\b|\bmake sure\b|\balways\b|\bnever\b|"
    r"\bfrom now on\b|\bgo ahead and\b|\bi want you to\b|\bwe need to\b|"
    r"\bit should\b|\bdid you\b|\bare you sure\b|\bi bet\b|\bi think\b)", re.I)


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} ledger-boundary {msg.rstrip()}\n")
    except Exception:
        pass


def read_tail(path, limit=400):
    out = []
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh.readlines()[-limit:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def human_text(rec):
    if rec.get("type") not in ("user", "human"):
        return None
    msg = rec.get("message") or rec
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return None


def already_swept(transcript):
    """True when this exact boundary was already announced and let through.

    Without this the hook would block the retry too, and the session could never
    commit. State is keyed on the transcript so a new session starts clean.
    """
    try:
        st = json.load(open(STATE))
        return st.get("transcript") == transcript and st.get("cleared") is True
    except Exception:
        return False


def mark_swept(transcript):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump({"transcript": transcript, "cleared": True,
                   "at": datetime.now(timezone.utc).isoformat()}, open(STATE, "w"))
    except Exception:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                       # fail OPEN on anything odd
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        cmd = ti.get("command", "") if isinstance(ti, dict) else ""
        if tool != "Bash" or not cmd or not BOUNDARIES.search(cmd):
            sys.exit(0)

        transcript = payload.get("transcript_path") or payload.get("transcriptPath") or ""
        if not transcript or not os.path.exists(transcript):
            log("ALLOW(no-transcript)")
            sys.exit(0)

        if already_swept(transcript):
            sys.exit(0)

        records = read_tail(transcript)

        # Find the last ledger write, then look only AFTER it. A session that
        # logs as it goes is never interrupted.
        last_write = -1
        for i, rec in enumerate(records):
            blob = json.dumps(rec)
            if any(v in blob for v in LEDGER_VERBS):
                last_write = i

        unlogged = []
        for rec in records[last_write + 1:]:
            txt = human_text(rec)
            if txt and QUALIFYING.search(txt):
                unlogged.append(" ".join(txt.split())[:110])

        if not unlogged:
            sys.exit(0)

        mark_swept(transcript)
        log(f"SWEEP {len(unlogged)} unlogged :: {cmd[:120]}")

        lines = "\n".join(f"    · \"{u}\"" for u in unlogged[-4:])
        print(
            "LEDGER BOUNDARY SWEEP — rule bbffc139 requires a sweep BEFORE a "
            "commit, push or deploy, and this is that moment.\n\n"
            f"{len(unlogged)} qualifying turn(s) since the last ledger write:\n"
            f"{lines}\n\n"
            "Re-read the exchange since that point and ask what of Joe's belongs "
            "in a ledger and is not stored — an override (log it even when he "
            "turns out wrong), a prediction with its reasoning AT decision time, "
            "a compression you then built on, a structural ruling, a corrected "
            "premise. Logging is free, binds nobody, and needs no permission.\n\n"
            "Log what qualifies, then re-run this command — it will go through.",
            file=sys.stderr)
        sys.exit(2)

    except Exception as exc:                       # fail OPEN
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
