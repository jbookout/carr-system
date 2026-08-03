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

GATE = os.path.expanduser("~/carr-system/hooks/record-home-gate.py")
VAULT = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

DENY, ALLOW = "deny", "allow"

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
    ("allow · hand-authored intake file in prospects/", ALLOW, "Write",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/Beasley-intake.md", "new_string": "x"}),
    ("allow · hand-authored enterprise file in prospects/", ALLOW, "Edit",
     {"file_path": f"{VAULT}/DNA/Clients/prospects/AltaPointe-enterprise.md", "new_string": "x"}),
    ("allow · a name not in DOSSIER_FILES", ALLOW, "Write",
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

    # ---- the allow half: ordinary narrative work must stay unblocked --------
    ("allow · doctrine edit under DNA/", ALLOW, "Edit",
     {"file_path": f"{VAULT}/DNA/writing-rules.md", "new_string": "no em-dashes"}),
    ("allow · CLAUDE.md ordinary edit", ALLOW, "Edit",
     {"file_path": f"{VAULT}/CLAUDE.md", "new_string": "rev 11: weekends are off"}),
    ("allow · NEW doctrine file under DNA/", ALLOW, "Write",
     {"file_path": f"{VAULT}/DNA/Marketing/new-playbook.md", "content": "playbook"}),
    ("allow · repo file, outside the vault", ALLOW, "Write",
     {"file_path": os.path.expanduser("~/carr-system/specs/some-spec.md"), "content": "spec"}),
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


def main():
    failed = 0
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
