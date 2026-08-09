#!/usr/bin/env python3
"""test-ledger-scope.py — the regression set for ledger-sweep.py's scope gate.

WHY THIS FILE EXISTS. The ledger hook has failed in BOTH directions, a year of
system-time apart, and each fix risks re-opening the other hole:

  MISSING (the original disease, rule bbffc139): zero entries across weeks,
  then five missed interventions in one night. Every historical miss is pinned
  here as a MUST-FIRE case. A tightening that silences any of them is a
  regression, not an improvement, and this file is what says so out loud.

  CRYING WOLF (2026-08-09, Joe's order "tighten the ledger triggers"): the hook
  fired twice inside a keyboard-repair thread carrying no CARR content at all —
  once on "i want it to be fully apple keyboard" (trigger 4's bare `i want`),
  once on "okay i think it worked" (trigger 2's bare `i think`). A gate that
  fires on ordinary conversation trains the eye to skip it, which is the exact
  failure the hook's own docstring warns about in health checks. Those are
  pinned as MUST-STAY-SILENT.

Run: .venv/bin/python tools/test-ledger-scope.py
Exit 0 = every case behaves; exit 1 = a named case regressed.
"""
import importlib.util
import os
import sys

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "hooks", "ledger-sweep.py")
spec = importlib.util.spec_from_file_location("ledger_sweep", HOOK)
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)


def fires(text, response_blob=""):
    """Does the hook fire on this human turn, given what the session did next?

    This MIRRORS main()'s decision order exactly — triggers, then the two
    suppressors, then the scope gate. It has to: a helper that tests a
    different sequence than the one that ships is a green light for code
    nobody runs (rule a9ecd5b4). test_end_to_end below spawns the real hook
    as a subprocess to prove this mirror has not drifted."""
    hits = [name for name, pat in ls.TRIGGERS if pat.search(text)]
    if ls.is_pure_question(text):
        hits = [h for h in hits if h not in ls.SUPPRESSIBLE_ON_QUESTION]
    if ls.is_past_outcome_report(text):
        hits = [h for h in hits if not h.startswith("2 ")]
    if not hits:
        return False
    return ls.exchange_touches_carr(text, response_blob)


# ── MUST FIRE — every one of these is a real intervention the hook exists for.
# The first seven are the historical misses named in ledger-sweep.py's own
# comments; silencing any of them re-opens the disease.
MUST_FIRE = [
    ("trigger 7 · hedged argument (2026-08-03 miss)",
     "I'll argue you on drive — the vault should stay where it is", ""),
    # THREE CASES BELOW CARRY A RESPONSE BLOB, and it is not a thumb on the
    # scale. Each of these turns happened mid-session during record-layer and
    # doctrine work, so in production the gate's second signal — what the
    # session was doing — was unmistakably present. Passing "" here would model
    # a situation that never occurred and would test a gate nobody ships. The
    # blobs are the minimum realistic content, not a generous one.
    ("trigger 7 · hedged recall (2026-08-03 miss)",
     "I'm almost positive we already addressed all these D's",
     "~/carr-system/exporters read-doctrine"),
    ("trigger 7 · hedged recall as question (2026-08-03 miss)",
     "I thought we had switched to refreshing the compiled rules every 30 minutes?", ""),
    ("trigger 7 · hedged state claim (2026-08-03 miss)",
     "I'm fairly certain the other session is done with the deal room.", ""),
    ("trigger 6 · socratic challenge (2026-08-02 miss)",
     "so everything is addressed in this session that is supposed to be right?",
     "~/carr-system/migrations mcp__b36e17b6__update-deal"),
    ("trigger 6 · conditional challenge (2026-08-02 miss)",
     "well if you detected the default failure mode protect against it",
     "~/carr-system/hooks/guard-unattended.py"),
    ("trigger 7 · flat correction (2026-08-03 miss)",
     "we just converted the vendor rows tonight", ""),
    ("joe's own words enacting the rule",
     "so whats it gonna take for you to start logging my personal observations bro. "
     "you haven't done it unprompted one single time since the rule was enacted", ""),
    # Real rulings that carry a CARR noun in the turn itself.
    ("trigger 4 · structural ruling on the record layer",
     "from now on every LOI goes out in Word, not PDF", ""),
    ("trigger 4 · ruling naming a client",
     "lets keep C-118 on the board but move him to nurture", ""),
    ("trigger 2 · prediction about the system",
     "i think the doctrine store cutover will take two weeks not five", ""),
    # A ruling with no CARR noun in the words, where the SESSION's work is the
    # signal — this is the case the scope gate must not eat.
    ("trigger 4 · bare ruling, but the session was in the repo",
     "lets always run the rehearsal on a branch first",
     "hooks/ledger-sweep.py ~/carr-system/migrations"),
    ("trigger 4 · bare ruling, session called a record verb",
     "from now on always attach the decision to the deal",
     "mcp__b36e17b6__log-decision"),
]

# ── MUST STAY SILENT — ordinary conversation with no CARR content.
MUST_BE_SILENT = [
    ("2026-08-09 false positive · keyboard request",
     "can you fix my logitech keyboard? i want it to be fully apple keyboard. "
     "rigth now its like a hybrid between apple and windows but ive become more "
     "comfortable with apple by now and i just want it to be the same as my laptop",
     "system_profiler ioreg defaults -currentHost"),
    ("2026-08-09 false positive · outcome report",
     "okay i think it worked", "defaults read -g"),
    ("chit-chat with a trigger word",
     "i think we should grab lunch", ""),
    ("hardware follow-up",
     "i want the f keys to do brightness like my laptop", "defaults read fnState"),
]

def test_end_to_end():
    """Spawn the REAL hook the way the harness does — a JSON payload on stdin
    naming a transcript — and read what it actually emits. Everything above
    imports the module and calls its pieces; only this proves the shipped
    entry point behaves, and that `fires()` above has not drifted from main().
    Modelled on guard-selftest.py, which spawns its hook rather than trusting
    an import."""
    import json
    import subprocess
    import tempfile

    cases = [
        ("keyboard turn must stay silent",
         "okay i think it worked",
         [{"type": "assistant", "message": {"content": [
             {"type": "text", "text": "defaults read -g fnState"}]}}],
         False),
        ("a real ruling must fire",
         "from now on every LOI goes out in Word, not PDF",
         [{"type": "assistant", "message": {"content": [
             {"type": "text", "text": "understood"}]}}],
         True),
    ]
    bad = []
    for label, human, after, want_fire in cases:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"type": "user", "origin": {"kind": "human"},
                                 "message": {"content": human}}) + "\n")
            for rec in after:
                fh.write(json.dumps(rec) + "\n")
            path = fh.name
        out = subprocess.run(
            [sys.executable, HOOK], input=json.dumps({"transcript_path": path}),
            capture_output=True, text=True, timeout=15)
        did_fire = "LEDGER SWEEP" in out.stdout
        os.unlink(path)
        if did_fire != want_fire:
            bad.append(f"END-TO-END — {label}: fired={did_fire}, wanted={want_fire}")
    return bad


fails = test_end_to_end()
for label, text, blob in MUST_FIRE:
    if not fires(text, blob):
        fails.append(f"REGRESSION (went silent, must fire) — {label}: {text[:70]!r}")
for label, text, blob in MUST_BE_SILENT:
    if fires(text, blob):
        fails.append(f"REGRESSION (fired, must be silent) — {label}: {text[:70]!r}")

total = len(MUST_FIRE) + len(MUST_BE_SILENT) + 2
if fails:
    print(f"FAIL — {len(fails)} of {total} cases regressed\n")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print(f"ok — {total}/{total} ledger-scope cases behave "
      f"({len(MUST_FIRE)} must-fire, {len(MUST_BE_SILENT)} must-stay-silent)")
sys.exit(0)
