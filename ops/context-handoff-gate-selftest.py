#!/usr/bin/env python3
"""context-handoff-gate-selftest.py — spawn the REAL hook and read its exit.

Rule a9ecd5b4: a success signal must be derived from evidence, not asserted. So
this does not import the hook and call its functions — it runs the hook as the
harness runs it (fresh process, JSON on stdin, JSON on stdout) and asserts on
what came back. Every case states the failure it is guarding against.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "hooks", "context-handoff-gate.py")

PASS: list[str] = []
FAIL: list[str] = []


def transcript(rows, tmpdir, name="t.jsonl"):
    path = os.path.join(tmpdir, name)
    with open(path, "w") as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return path


def usage_row(total):
    """An assistant row whose three usage fields sum to `total`."""
    return {"type": "assistant", "message": {"usage": {
        "input_tokens": 2,
        "cache_creation_input_tokens": 98,
        "cache_read_input_tokens": total - 100,
    }}}


def run(payload, env=None, statefile=None):
    e = dict(os.environ)
    e["CARR_CONTEXT_WINDOW"] = "1000000"
    if statefile:
        e["CARR_CONTEXT_STATE"] = statefile
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, env=e)
    out = p.stdout.strip()
    parsed = None
    if out:
        try:
            parsed = json.loads(out)
        except Exception:
            parsed = {"_unparseable": out}
    return p.returncode, parsed


def said(out):
    """The gate's announcement text, or "" for silence.

    DEMOTED 2026-08-23 (the gates-audit council's Stop-gate rationing, Joe's
    order). This gate emitted {"decision": "block"}, which reopens the turn; it
    now announces the same text into context without forcing another message.
    So every "fires" assertion below reads the announcement, and a payload still
    carrying `decision` reads as SILENCE on purpose — a block wearing an
    announce's clothes must fail these, not pass them.
    """
    if not isinstance(out, dict):
        return ""
    if "decision" in out:
        return ""
    return (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{(' :: ' + detail) if detail and not cond else ''}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "state.json")

        # --- the threshold itself -------------------------------------------
        # Guards: an off-by-one that fires a band early, or never.
        t69 = transcript([usage_row(699_000)], tmp, "t69.jsonl")
        rc, out = run({"session_id": "s-under", "transcript_path": t69}, statefile=state)
        check("69.9% does not fire", rc == 0 and out is None, f"rc={rc} out={out}")

        t70 = transcript([usage_row(700_000)], tmp, "t70.jsonl")
        rc, out = run({"session_id": "s-at70", "transcript_path": t70}, statefile=state)
        check("exactly 70% fires", rc == 0 and bool(said(out)),
              f"rc={rc} out={out}")
        check("70% reason names the band and the real numbers",
              "70%" in said(out) and "700,000" in said(out))
        check("70% reason orders the packet + the chip",
              "handoff" in said(out)
              and "spawn_task" in said(out))
        check("70% reason is NOT the hard-line text",
              "HARD LINE" not in said(out))
        # THE LAST MILE (2026-08-10, decision aa6c00fa). The chip renders on the
        # desktop app only, so a handoff delivered by chip alone is invisible to
        # Joe in the field. Every band must order the push; without this
        # assertion the delivery half is exactly as untested as it was the night
        # Joe found the defect by looking at his phone.
        check("70% reason orders the phone push",
              "PushNotification" in said(out))
        check("70% reason says why the push is needed (desktop-only chip)",
              "desktop app only" in said(out))
        # The 70% band must NOT queue a scheduled continuation: the session
        # usually keeps working past 70, so a queued run would duplicate it.
        check("70% band does NOT queue a scheduled continuation",
              "create_scheduled_task" not in said(out))
        check("70% reason leads the push with the disposition",
              "LEAD WITH THE DISPOSITION" in said(out)
              and "truncates the TAIL" in said(out))

        # --- fires once per band, then escalates ----------------------------
        # Guards the loop risk (a Stop hook that blocks forever strands Joe)
        # AND the opposite failure: a one-shot nudge that never escalates.
        s = "s-bands"
        t75 = transcript([usage_row(750_000)], tmp, "t75.jsonl")
        rc, out = run({"session_id": s, "transcript_path": t75}, statefile=state)
        check("first crossing of band 70 speaks", bool(said(out)))
        # The top-level key, not a substring search: the packet instructions
        # themselves say "decisions already settled", which made the first
        # version of this assertion fail on its own fixture text.
        check("...and it does not reopen the turn to speak",
              rc == 0 and "decision" not in (out or {}), f"rc={rc} keys={list((out or {}))}")
        rc, out = run({"session_id": s, "transcript_path": t75}, statefile=state)
        check("band 70 does not fire twice (no nag)", out is None, f"out={out}")

        t90 = transcript([usage_row(900_000)], tmp, "t90.jsonl")
        rc, out = run({"session_id": s, "transcript_path": t90}, statefile=state)
        check("same session escalates to band 88", bool(said(out)))
        check("band 88 uses the hard-line text",
              "HARD LINE" in said(out))
        # At the hard line the session IS stopping, so both delivery routes are
        # required: the push so Joe learns of it wherever he is, and the
        # one-time scheduled task so the continuation resumes with no click.
        check("band 88 orders the phone push",
              "PushNotification" in said(out))
        check("band 88 queues a scheduled continuation (removes the click)",
              "create_scheduled_task" in said(out)
              and "fireAt" in said(out))
        check("band 88 names a collision-proof taskId for it",
              "handoff-continuation-" in said(out))
        # The overclaim guard. A scheduled task does not run on a closed
        # machine, so the text must promise that the work RESUMES, never that
        # it runs at a particular time. This is the same class of honesty the
        # file already keeps about the chip not being a new session.
        # Joe, on the first live push, phone closed: "i dont know if it did
        # anything." Tapping the push opens the EXHAUSTED session, not the
        # continuation, so a message naming only the subject strands him. Both
        # bands must order the disposition in the same line.
        # The ORDER is the rule. A lock screen truncates the TAIL at roughly
        # 100 chars, so a disposition written last is the part the phone eats —
        # which is exactly what the first corrected push got wrong (163 chars,
        # "Nothing needed from you" at the end).
        check("band 88 orders disposition FIRST, not merely present",
              "DISPOSITION FIRST" in said(out))
        check("band 88 gives both permitted opening stems",
              "Nothing needed from you" in said(out)
              and "Need your call on" in said(out))
        check("band 88 names the truncation limit that forces the order",
              "100 characters" in said(out))
        check("band 88 does not promise the continuation runs unattended",
              "unattended" not in said(out).lower()
              and "only while the app is open" in said(out))
        rc, out = run({"session_id": s, "transcript_path": t90}, statefile=state)
        check("band 88 does not fire twice", out is None, f"out={out}")

        # A DIFFERENT session at the same size must still fire — guards a state
        # key collision that would silently disable the gate for everyone after
        # the first session crossed.
        rc, out = run({"session_id": "s-other", "transcript_path": t90}, statefile=state)
        check("state is per-session, not global", bool(said(out)))

        # --- reads the LAST usage row, not the largest ----------------------
        # Guards a max() implementation: after a compaction the context DROPS,
        # and a max() would keep reporting the pre-compaction peak forever.
        drop = transcript([usage_row(900_000), usage_row(120_000)], tmp, "drop.jsonl")
        rc, out = run({"session_id": "s-drop", "transcript_path": drop}, statefile=state)
        check("post-drop context is read from the last row (allows)", out is None, f"out={out}")

        # --- fail-open paths -------------------------------------------------
        # Every one of these must let the turn END. A gate that blocks on its
        # own bug is worse than no gate.
        rc, out = run({"session_id": "s-x", "transcript_path": "/nope/missing.jsonl"},
                      statefile=state)
        check("missing transcript fails open", rc == 0 and out is None)

        empty = transcript([], tmp, "empty.jsonl")
        rc, out = run({"session_id": "s-e", "transcript_path": empty}, statefile=state)
        check("empty transcript fails open", rc == 0 and out is None)

        nousage = transcript([{"type": "user", "message": {"content": "hi"}}], tmp, "nu.jsonl")
        rc, out = run({"session_id": "s-n", "transcript_path": nousage}, statefile=state)
        check("transcript with no usage rows fails open", rc == 0 and out is None)

        rc, out = run({}, statefile=state)
        check("payload with no transcript_path fails open", rc == 0 and out is None)

        p = subprocess.run([sys.executable, HOOK], input="not json at all",
                           capture_output=True, text=True)
        check("garbage stdin fails open", p.returncode == 0 and not p.stdout.strip())

        # Malformed lines interleaved with good ones — a real transcript can be
        # torn mid-write while the session is live.
        torn = transcript(["{not json", usage_row(800_000), "]]broken"], tmp, "torn.jsonl")
        rc, out = run({"session_id": "s-torn", "transcript_path": torn}, statefile=state)
        check("torn lines are skipped, good rows still counted",
              bool(said(out)), f"out={out}")

        # --- window override is honoured -------------------------------------
        # Guards the 200K-window case: if the harness ever changes, the gate
        # must fire EARLIER, not silently never.
        rc, out = run({"session_id": "s-200k", "transcript_path": transcript(
            [usage_row(150_000)], tmp, "small.jsonl")},
            env={"CARR_CONTEXT_WINDOW": "200000"}, statefile=state)
        check("200K window: 150K fires at 75%", bool(said(out)))

        # --- against a REAL transcript ---------------------------------------
        # The synthetic rows above are my own shape assumption; this is the only
        # case that proves the parser matches what the product actually writes.
        # Was pinned to "-Users-booko-My-Drive-CARR-AI", which is one machine's
        # project folder and contains a username. On any other Mac that path
        # cannot exist, so this fell to the else branch and FAILED the whole
        # selftest — which in turn aborted the migration script that runs it
        # (2026-08-10 fresh-machine audit). Search every project instead.
        root = os.path.expanduser("~/.claude/projects")
        files = []
        if os.path.isdir(root):
            for d in os.listdir(root):
                sub = os.path.join(root, d)
                if not os.path.isdir(sub):
                    continue
                files += [os.path.join(sub, f) for f in os.listdir(sub)
                          if f.endswith(".jsonl")
                          and os.path.getsize(os.path.join(sub, f)) > 50_000]
        real = max(files, key=os.path.getsize) if files else None
        if real:
            sys.path.insert(0, HERE)
            import importlib.util
            spec = importlib.util.spec_from_file_location("chg", HOOK)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            n = mod.context_tokens(real)
            check("real transcript parses to a plausible context size",
                  isinstance(n, int) and 1_000 < n < 1_100_000,
                  f"got {n} from {os.path.basename(real)}")
            print(f"       (real session {os.path.basename(real)[:12]}… "
                  f"= {n:,} tokens = {100*n/1_000_000:.1f}% of 1M)")
        else:
            # A machine with no Claude history yet has nothing to parse. That is
            # a fresh install, not a defect — failing here made a correct
            # migration report MIGRATION INCOMPLETE. Say it was skipped, out
            # loud, so a green run never hides which half was exercised.
            print("       SKIP  real-transcript case — no session ≥50KB on this "
                  "machine yet (fresh install); synthetic cases above still ran")

    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
