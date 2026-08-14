#!/usr/bin/env python3
"""test-record-home-gate.py — proves the record-home gate denies what it claims
and, just as importantly, ALLOWS ordinary narrative editing.

The allow cases are the point. A deny gate that also blocks doctrine edits gets
disabled within a day, and then it protects nothing.

    .venv/bin/python tools/test-record-home-gate.py     # exit 0 = all pass
"""
import json
import os
import subprocess
import sys

# Script-relative, NOT expanduser("~/carr-system") — see the same fix in
# test-ledger-sweep.py. A checkout outside $HOME made this file crash on import
# rather than report a failure.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "record-home-gate.py")
VAULT = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

DENY, ALLOW = "deny", "allow"

# THIS MACHINE'S VERDICT FOR A JOB-OUTPUT PATH, and why it is computed instead
# of written down. Those rows retire PER PARTNER (Joe migrated 2026-08-14, Dell
# at the 8/21 cutoff), so the honest expectation for a real hook invocation
# depends on whose machine is running the test. Hardcoding Joe's answer passed
# on his Mac and failed on the GitHub runner, which has no identity file and so
# resolves as unmigrated — caught by CI on the first push of that change.
#
# The per-partner LOGIC is pinned exactly by the unit cases below, which inject
# the actor and need no machine. What these subprocess cases prove is the layer
# above it: that the hook actually consults the manifest and applies its verdict
# end to end, on whatever machine is running.
sys.path.insert(0, os.path.join(REPO, "hooks"))
from md_manifest import MIGRATED_PARTNERS, local_actor  # noqa: E402

JOB_OUTPUT = DENY if local_actor() in MIGRATED_PARTNERS else ALLOW

CASES = [
    # (label, expected, tool, tool_input)
    ("A · generated render (shared rules)", DENY, "Write",
     {"file_path": f"{VAULT}/DNA/compiled-rules-shared.md", "content": "x"}),
    ("A · generated render (open-loops)", DENY, "Edit",
     {"file_path": f"{VAULT}/00_Context/open-loops.md", "new_string": "x"}),
    ("A · generated render (non-md: deals json)", DENY, "Write",
     {"file_path": f"{VAULT}/DNA/Deal Management/panhandle-team-deals.json", "content": "{}"}),

    # --- the 2026-08-03 IT sweep found 27 of 41 targets unguarded. These four
    # were all in that gap; they pass only because the set is now parsed from
    # exporters/targets.py instead of retyped.
    ("A · client dossier (the worst case, list-guarded)", DENY, "Edit",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/LifeDentalGroup.md", "new_string": "deal update"}),
    # NOT a blanket directory rule, and that distinction is load-bearing:
    # prospects/ holds 23 GENERATED dossiers alongside hand-authored files the
    # client-intake agent writes on purpose. Guarding the directory blocked those
    # too, and over-blocking a partner's own writing surface is how a gate ends up
    # switched off. The set is the exporter's own DOSSIER_FILES list.
    ("P0+ · intake file in prospects/ follows this machine's partner", JOB_OUTPUT, "Write",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/Beasley-intake.md", "new_string": "x"}),
    ("P0+ · enterprise file in prospects/ follows this machine's partner", JOB_OUTPUT, "Edit",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/AltaPointe-enterprise.md", "new_string": "x"}),
    ("P0+ · a name not in DOSSIER_FILES is DENIED too (closed 2026-08-14)", JOB_OUTPUT, "Write",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/BrandNewClient.md", "content": "x"}),
    ("A · hunt-ledger (was unguarded)", DENY, "Edit",
     {"file_path": f"{VAULT}/DNA/Network/hunt-ledger.md", "new_string": "x"}),
    ("A · record-layer-dictionary (was unguarded)", DENY, "Edit",
     {"file_path": f"{VAULT}/DNA/Team/record-layer-dictionary.md", "new_string": "x"}),

    ("B · NEW md in 00_Context/", DENY, "Write",
     {"file_path": f"{VAULT}/00_Context/audit-findings-2026-08-03.md", "content": "findings"}),
    ("B · NEW md at the vault root", DENY, "Write",
     {"file_path": f"{VAULT}/DECISIONS.md", "content": "we decided"}),

    ("C · handoff, new file", DENY, "Write",
     {"file_path": f"{VAULT}/00_Context/handoffs/handoff-2026-08-04-thing.md", "content": "x"}),
    ("C · handoff, EDIT to an existing one", DENY, "Edit",
     {"file_path": f"{VAULT}/00_Context/handoffs/handoff-2026-08-02-rule-curation.md",
      "new_string": "session 2 delta"}),

    ("D · rule uuid + rule verb in a narrative file", DENY, "Edit",
     {"file_path": f"{VAULT}/CLAUDE.md",
      "new_string": "activate-rule on 3bc574d9-f672-4619-9624-13ffef436a3f"}),

    # ---- PHASE 0 (2026-08-07, decision 82a2fb62): deny-by-default flips the
    # old "doctrine editing stays open" posture. Doctrine edits are now DENIED —
    # content routes through verbs until the store's write verbs land (P2) and
    # the corpus migrates (P4/P5). The allow set is the exact-path manifest
    # (hooks/md_manifest.py) plus the retiring job-output prefixes.
    ("P0 · doctrine edit under DNA/ now DENIED", DENY, "Edit",
     {"file_path": f"{VAULT}/DNA/writing-rules.md", "new_string": "no em-dashes"}),
    ("P0 · NEW doctrine file under DNA/ now DENIED", DENY, "Write",
     {"file_path": f"{VAULT}/DNA/Marketing/new-playbook.md", "content": "playbook"}),
    ("P0 · idea-inbox distillation now DENIED", DENY, "Write",
     {"file_path": f"{VAULT}/00_Context/idea-inbox/2026-08-08-some-study.md", "content": "x"}),
    ("P0 · INDEX.md edit now DENIED (migrates in P4)", DENY, "Edit",
     {"file_path": f"{VAULT}/INDEX.md", "new_string": "x"}),
    ("P0 · uppercase extension does not slip through", DENY, "Write",
     {"file_path": f"{VAULT}/DNA/Team/notes.MD", "content": "x"}),
    ("allow · CLAUDE.md ordinary edit (manifest exact)", ALLOW, "Edit",
     {"file_path": f"{VAULT}/CLAUDE.md", "new_string": "rev 11: weekends are off"}),
    ("allow · AGENTS.md edit (manifest exact)", ALLOW, "Edit",
     {"file_path": f"{VAULT}/AGENTS.md", "new_string": "x"}),
    ("P0+ · weekly brief output follows this machine's partner", JOB_OUTPUT, "Write",
     {"file_path": f"{VAULT}/DNA/Network/briefs/2026-08-10-network-brief.md", "content": "x"}),
    ("allow · repo file, outside the vault", ALLOW, "Write",
     {"file_path": os.path.join(REPO, "specs", "some-spec.md"), "content": "spec"}),
    ("allow · scratchpad", ALLOW, "Write",
     {"file_path": "/private/tmp/claude-501/scratch/notes.md", "content": "scratch"}),
    ("allow · non-markdown in the vault", ALLOW, "Write",
     {"file_path": f"{VAULT}/Automation/lead-board.html", "content": "<html>"}),
    ("allow · bare uuid with no rule verb", ALLOW, "Edit",
     {"file_path": f"{VAULT}/CLAUDE.md",
      "new_string": "session id 3bc574d9-f672-4619-9624-13ffef436a3f"}),
    ("allow · a tool the gate does not cover", ALLOW, "Bash",
     {"command": f"rm {VAULT}/x.md"}),
]


def run(tool, ti):
    p = subprocess.run([sys.executable, GATE],
                       input=json.dumps({"tool_name": tool, "tool_input": ti}),
                       capture_output=True, text=True, timeout=20)
    return p.returncode, (p.stderr or "").strip()


def manifest_unit_cases():
    """Direct md_manifest checks the subprocess harness cannot reach: the
    retirement clock is injectable only in-process."""
    sys.path.insert(0, os.path.join(REPO, "hooks"))
    from datetime import date, timedelta
    from md_manifest import md_write_verdict, CUTOFF
    cases = [
        # CLOSED EARLY, PER PARTNER — Joe, 2026-08-14: he is fully migrated, so
        # his side retires now and absorbs the breakage while Dell's still
        # works. The staggering IS the requirement, so both halves are asserted:
        # closing Dell early would be as wrong as leaving Joe open.
        ("unit · a migrated partner's brief row is CLOSED before the old cutoff",
         md_write_verdict("DNA/Network/briefs/x.md", today=CUTOFF,
                          actor="joe") is not None),
        ("unit · an UNMIGRATED partner keeps the row until the cutoff",
         md_write_verdict("DNA/Network/briefs/x.md", today=CUTOFF,
                          actor="dell") is None),
        ("unit · and the unmigrated partner still closes AFTER the cutoff",
         md_write_verdict("DNA/Network/briefs/x.md", today=CUTOFF + timedelta(days=1),
                          actor="dell") is not None),
        ("unit · an UNRESOLVABLE identity falls back to the later date, so a "
         "broken identity file cannot retire an unmigrated partner early",
         md_write_verdict("DNA/Network/briefs/x.md", today=CUTOFF,
                          actor="unresolved-machine") is None),
        ("unit · the social batch path is closed for a migrated partner",
         md_write_verdict("Marketing/Social Media/x-batch-2026-08-24-week.md",
                          today=CUTOFF, actor="joe") is not None),
        ("unit · closed-row message names the ruling, not just the cutoff",
         "eliminate the ability" in (md_write_verdict(
             "DNA/Network/briefs/x.md", today=CUTOFF, actor="joe") or "")),
        ("unit · brief prefix DENIED after cutoff",
         md_write_verdict("DNA/Network/briefs/x.md",
                          today=CUTOFF + timedelta(days=1)) is not None),
        ("unit · prospects prefix DENIED after cutoff",
         md_write_verdict("DNA/Clients/prospects/Beasley-intake.md",
                          today=CUTOFF + timedelta(days=1)) is not None),
        ("unit · deny message names the verbs",
         "log-decision" in (md_write_verdict("DNA/writing-rules.md") or "")),
        ("unit · exact allow is not a prefix (CLAUDE.md.bak-style)",
         md_write_verdict("CLAUDE.md.old.md") is not None),
    ]
    return cases


def main():
    failed = 0
    for label, ok in manifest_unit_cases():
        failed += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    for label, expected, tool, ti in CASES:
        rc, err = run(tool, ti)
        got = DENY if rc == 2 else ALLOW
        ok = got == expected
        failed += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}  (expected {expected}, got {got})")
        if not ok and err:
            print(f"          stderr: {err[:160]}")
        if ok and expected == DENY and not err:
            print("          FAIL: denied with no reason on stderr")
            failed += 1
    print(f"\npassed {len(CASES) - failed} · failed {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
