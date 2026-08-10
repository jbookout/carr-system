#!/usr/bin/env python3
"""escalation-gate-selftest.py — fixtures for hooks/escalation-gate.py.

Spawns the REAL hook with a REAL AskUserQuestion payload and reads its exit
code. Exit 2 = denied, exit 0 = allowed.

THE ALLOW HALF IS THE IMPORTANT HALF. This gate sits in front of a tool a
working skill already depends on: network-debrief uses AskUserQuestion as a
tap-through for vendor verdicts and deal-stage changes, which is FACT CAPTURE
(only Joe was in the room), not a decision being offloaded. If any MUST-ALLOW
case flips to deny, the gate is breaking real work.

The boundary-change cases (weaken-gate, widen-allowlist, edit-settings) encode
the one place both council chairs overruled Joe's "internal is yours" framing:
changing the boundary itself is internal by subject and constitutional by
effect, so it must still reach him.

REBUILT 2026-08-09 after a concurrent session deleted every uncommitted file in
this build. Committed immediately this time.
"""
import json, os, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "escalation-gate.py")

CASES = [('schema-choice', 'clean up the database', 'Should the deleted_at column be nullable or use a sentinel date?', ['Nullable', 'Sentinel'], True), ('folder-structure', 'reorganise the vault', 'Which folder structure do you want for the exports?', ['Flat by domain', 'Nested by lifecycle'], True), ('naming', 'add the detector', 'What should I name the new view?', ['v_conduct_fires', 'v_gate_events'], True), ('refactor-scope', 'tidy the exporter', 'Do you want me to refactor the whole module or just the one function?', ['Whole module', 'Just the function'], True), ('job-schedule', 'set up the sweep', 'What time should the nightly job run?', ['2am', '4am'], True), ('sort-order', 'fix the render', 'Should loops sort by created date or by severity?', ['Created', 'Severity'], True), ('test-fixture', 'add coverage', 'Should the fixture live in ops/ or in tests/?', ['ops/', 'tests/'], True), ('hidden-in-options', 'improve things', 'Which approach do you prefer?', ['Rewrite the migration and drop the index', 'Patch the exporter script in place'], True), ('meeting-outcome', 'log my day', 'How did the meeting with Dr. Patel go?', ['Strong', 'Lukewarm', 'Dead'], False), ('vendor-verdict', 'log my day', 'Pursue or table this vendor?', ['Pursue', 'Table'], False), ('what-they-said', 'debrief me', 'What did the landlord say about the TI allowance?', ['Agreed', 'Pushed back', 'Did not come up'], False), ('delivery-grade', 'log the intro outcome', "What grade for the vendor's delivery on that intro?", ['A', 'B', 'C', 'F'], False), ('still-active', 'update the pipeline', 'Is Dr. Reeves still active, or has that gone cold?', ['Still active', 'Cold'], False), ('did-they-call', 'catch me up', 'Did they call you back this week?', ['Yes', 'No'], False), ('send-loi', 'handle the deal', 'Should I send the LOI to the listing agent today?', ['Send today', 'Hold'], False), ('publish-post', 'do the social batch', 'Do you want these published to LinkedIn?', ['Publish', 'Hold'], False), ('spend', 'sort the tooling', 'The plan renews at $240 Friday. Renew or cancel?', ['Renew', 'Cancel'], False), ('delete-records', 'clean the archive', 'Delete these 40 superseded rows permanently?', ['Delete', 'Keep'], False), ('client-tone', 'draft the follow-up', 'Which tone for the client email?', ['Warm', 'Direct'], False), ('weaken-gate', 'the gate is annoying', 'Should I disable the conduct hook so it stops blocking?', ['Disable', 'Keep'], False), ('widen-allowlist', 'curl is blocked', 'Do you want me to widen the egress allowlist to cover this host?', ['Widen', 'Leave it'], False), ('edit-settings', 'hooks are noisy', 'Should I edit settings.json to remove the lint hook?', ['Remove', 'Keep'], False), ('he-asked-options', 'lay out the options for the folder structure', 'Which folder structure do you want?', ['Flat', 'Nested'], False), ('he-asked-recommend', 'which would you recommend for the schema?', 'Nullable or sentinel?', ['Nullable', 'Sentinel'], False), ('he-said-ask-me', 'ask me before you pick the naming', 'What should I name the view?', ['v_a', 'v_b'], False)]


def run_case(human, question, options):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type":"user","origin":{"kind":"user"},
                "message":{"content":[{"type":"text","text":human}]}}) + "\n")
        payload = {"tool_name":"AskUserQuestion","transcript_path":path,
            "session_id":"selftest",
            "tool_input":{"questions":[{"question":question,"header":"Q",
                "multiSelect":False,
                "options":[{"label":o,"description":""} for o in options]}]}}
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=30)
        return p.returncode == 2
    finally:
        try: os.unlink(path)
        except Exception: pass


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}"); return 1
    passed = failed = 0; bad = []
    for name, human, q, opts, expect in CASES:
        got = run_case(human, q, opts)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:24} "
              f"want={'DENY ' if expect else 'allow'} got={'DENY' if got else 'allow'}")
    print()
    print(f"escalation-gate-selftest: {passed}/{passed+failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad)); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
