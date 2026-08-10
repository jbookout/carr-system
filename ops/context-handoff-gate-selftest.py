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

PASS, FAIL = [], []


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
        check("exactly 70% fires", rc == 0 and out and out.get("decision") == "block",
              f"rc={rc} out={out}")
        check("70% reason names the band and the real numbers",
              bool(out) and "70%" in out.get("reason", "") and "700,000" in out.get("reason", ""))
        check("70% reason orders the packet + the chip",
              bool(out) and "handoff" in out.get("reason", "")
              and "spawn_task" in out.get("reason", ""))
        check("70% reason is NOT the hard-line text",
              bool(out) and "HARD LINE" not in out.get("reason", ""))

        # --- fires once per band, then escalates ----------------------------
        # Guards the loop risk (a Stop hook that blocks forever strands Joe)
        # AND the opposite failure: a one-shot nudge that never escalates.
        s = "s-bands"
        t75 = transcript([usage_row(750_000)], tmp, "t75.jsonl")
        rc, out = run({"session_id": s, "transcript_path": t75}, statefile=state)
        check("first crossing of band 70 blocks", out and out.get("decision") == "block")
        rc, out = run({"session_id": s, "transcript_path": t75}, statefile=state)
        check("band 70 does not fire twice (no loop)", out is None, f"out={out}")

        t90 = transcript([usage_row(900_000)], tmp, "t90.jsonl")
        rc, out = run({"session_id": s, "transcript_path": t90}, statefile=state)
        check("same session escalates to band 88", out and out.get("decision") == "block")
        check("band 88 uses the hard-line text",
              bool(out) and "HARD LINE" in out.get("reason", ""))
        rc, out = run({"session_id": s, "transcript_path": t90}, statefile=state)
        check("band 88 does not fire twice", out is None, f"out={out}")

        # A DIFFERENT session at the same size must still fire — guards a state
        # key collision that would silently disable the gate for everyone after
        # the first session crossed.
        rc, out = run({"session_id": "s-other", "transcript_path": t90}, statefile=state)
        check("state is per-session, not global", out and out.get("decision") == "block")

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
              out and out.get("decision") == "block", f"out={out}")

        # --- window override is honoured -------------------------------------
        # Guards the 200K-window case: if the harness ever changes, the gate
        # must fire EARLIER, not silently never.
        rc, out = run({"session_id": "s-200k", "transcript_path": transcript(
            [usage_row(150_000)], tmp, "small.jsonl")},
            env={"CARR_CONTEXT_WINDOW": "200000"}, statefile=state)
        check("200K window: 150K fires at 75%", out and out.get("decision") == "block")

        # --- against a REAL transcript ---------------------------------------
        # The synthetic rows above are my own shape assumption; this is the only
        # case that proves the parser matches what the product actually writes.
        proj = os.path.expanduser("~/.claude/projects/-Users-booko-My-Drive-CARR-AI")
        real = None
        if os.path.isdir(proj):
            files = [os.path.join(proj, f) for f in os.listdir(proj) if f.endswith(".jsonl")]
            files = [f for f in files if os.path.getsize(f) > 50_000]
            if files:
                real = max(files, key=os.path.getsize)
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
            check("real transcript available to test against", False, "none found")

    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
