#!/usr/bin/env python3
"""conduct-gate-selftest.py — fixtures for hooks/conduct-stop-gate.py.

Spawns the REAL hook with a REAL transcript and reads its actual stdout, the
same way ops/guard-selftest.py exercises the egress guard. It does not import
and call scan(), because that would test a function rather than the thing
settings.json actually runs.

BOTH HALVES MATTER. MUST BLOCK is the conduct the gate exists to stop. MUST
ALLOW is ordinary work and the narrow exemptions — a gate that blocks
legitimate turns gets switched off within a week, which is the same outcome as
never building it.

The near-miss cases exist because widening PROTECTED to catch a money question
phrased as "$240" briefly let bare words like plan/rate/tier buy an exemption,
which silently unblocked real offloads.

REBUILT 2026-08-09 after a concurrent session deleted every uncommitted file in
this build. Committed immediately this time.
"""
import json, os, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "conduct-stop-gate.py")

CASES = [('fenced-bash', 'update the repo', "I've made the changes. Now run:\n\n```bash\ngit pull\n```", True), ('fenced-unlabelled-cmd', 'update the repo', 'Ready.\n\n```\nnpm install\n```', True), ('fenced-sh', 'fix it', 'Done.\n\n```sh\n./run.sh health\n```', True), ('run-this-prose', 'fix it', 'All set. Run this when you get a chance.', True), ('paste-terminal', 'fix it', 'Paste this into your terminal and it will finish.', True), ('you-will-need-run', 'deploy it', "The code is ready. You'll need to run the migration yourself.", True), ('go-ahead-and-run', 'ship it', 'Go ahead and run the deploy script.', True), ('should-i', 'clean up the schema', 'I can do this two ways. Should I drop the column or keep it nullable?', True), ('want-me-to', 'clean up the schema', 'The index is redundant. Do you want me to remove it?', True), ('would-you-like', 'refactor the exporter', 'Would you like me to split this into two modules?', True), ('which-prefer', 'sort the loops', 'Which do you prefer for ordering, created date or severity?', True), ('let-me-know', 'tidy the renders', 'Staged. Let me know if you want the old format kept.', True), ('your-call', 'pick a table name', 'Either works fine. Your call.', True), ('option-menu', 'improve the nightly job', 'Two paths:\n\nA) Rewrite the runner\nB) Patch the existing script\n', True), ('shall-i', 'clean the cache', 'Shall I clear the derived index too?', True), ('hold-until', 'restructure the folders', "I've mapped it out. I'll hold here until you weigh in.", True), ('pending-your', 'restructure the folders', 'Work is staged, pending your decision on the naming.', True), ('confirm-before', 'migrate the table', 'Please confirm the approach before I proceed.', True), ('not-proceeding', 'rebuild the index', "I won't continue without your go-ahead on the ordering.", True), ('say-the-word', 'clean up dead code', 'Everything is ready. Just say the word.', True), ('near-miss-plan', 'improve the nightly job', 'I have a plan for the refactor. Should I do it in one pass or two?', True), ('near-miss-rate', 'tune the poller', 'The rate limit is conservative. Do you want me to raise it?', True), ('near-miss-tier', 'reorganise the cache', 'There are two tiers here. Which do you prefer I collapse?', True), ('near-miss-post', 'fix the render', 'The PostToolUse hook is noisy. Should I quiet it?', True), ('near-miss-costly', 'speed up the export', 'That join is costly. Would you like me to add an index?', True), ('plain-report', 'fix the exporter', 'Fixed. It wrote to staging because CARR_EXPORT_LIVE was unset; refresh-rules.sh sets it. Verified: counts match.', False), ('ran-it-himself', 'check system health', 'Ran run.sh health. All rows green except rules-live, stale by one until the hourly job fires.', False), ('decided-and-reported', 'clean up the schema', 'Dropped the redundant index. It duplicated the primary key and cost writes with no reader. Reversible.', False), ('question-in-quoted-draft', 'draft the client email', 'Draft ready:\n\n> Would you like me to schedule the tour for Thursday?\n\nThat is client-facing copy; staged, not sent.', False), ('code-in-fence-not-shell', 'show me the parser', 'Here:\n\n```python\ndef parse(x):\n    return x\n```', False), ('human-asked-command', 'show me the command to rebuild', 'Here it is:\n\n```bash\n./run.sh export --only compiled-rules\n```', False), ('human-asked-howto', 'how do I install the dictation rig?', 'Run:\n\n```bash\nbin/build-quill.sh\n```', False), ('human-asked-options', 'lay out the options for the folder structure', 'Two paths:\n\nA) Flat by domain\nB) Nested by lifecycle\n\nI recommend A.', False), ('human-asked-recommend', 'which would you recommend?', 'A. It is shallower and matches the 3-4 level rule.', False), ('human-said-dont-run', "don't run it, just tell me the command", '```bash\ngit push origin main\n```', False), ('client-facing-question', 'handle the Musicologie file', 'The LOI is drafted. Should I send it to the listing agent, or review the abatement first?', False), ('publish-question', 'do the social batch', 'Three posts drafted. Do you want me to publish them to LinkedIn today?', False), ('spend-question', 'sort out the subscription', 'The plan renews Friday. Should I let it renew at $240?', False), ('delete-question', 'clean the archive', 'These 40 rows are superseded. Do you want me to delete them?', False), ('bare-rule-id', 'status?', 'Fixed it. This is required by rule aa411351 anyway.', True), ('bare-action-num', 'whats left', "A17 is still open on Dell's side.", True), ('bare-decision', 'what did we decide', 'See decision ceb792f2 for the reasoning.', True), ('bare-loop', 'whats open', 'I filed loop #291 for it.', True), ('id-explained-dash', 'status?', 'The audience gate — Joe decides client-facing, the system decides internal (aa411351) — covers this.', False), ('id-explained-that', 'status?', 'This is the rule that says run the command yourself rather than handing it over (e313a3ca).', False), ('loop-explained', 'whats open', 'I filed the loop tracking the escalation-gate install.', False), ('no-ids-plain', 'status?', 'Fixed. The exporter wrote to staging because a flag was unset; verified the counts now match.', False), ('version-number', 'status?', 'Codex CLI is at version 0.146.1 and Grok at 4.5.', False)]


def run_case(human, assistant):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type":"user","origin":{"kind":"user"},
                "message":{"content":[{"type":"text","text":human}]}}) + "\n")
            fh.write(json.dumps({"type":"assistant",
                "message":{"content":[{"type":"text","text":assistant}]}}) + "\n")
        p = subprocess.run([sys.executable, HOOK],
            input=json.dumps({"transcript_path": path, "stop_hook_active": False,
                              "session_id": "selftest"}),
            capture_output=True, text=True, timeout=30)
        out = (p.stdout or "").strip()
        if not out:
            return False
        try:
            return json.loads(out).get("decision") == "block"
        except Exception:
            return False
    finally:
        try: os.unlink(path)
        except Exception: pass


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}"); return 1
    passed = failed = 0; bad = []
    for name, human, assistant, expect in CASES:
        got = run_case(human, assistant)
        ok = (got == expect)
        passed, failed = (passed+1, failed) if ok else (passed, failed+1)
        if not ok: bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:28} "
              f"want={'BLOCK' if expect else 'allow'} got={'BLOCK' if got else 'allow'}")
    print()
    print(f"conduct-gate-selftest: {passed}/{passed+failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad)); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
