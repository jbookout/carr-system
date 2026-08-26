#!/usr/bin/env python3
"""stale-claim-gate-selftest.py — fixtures for hooks/stale-claim-gate.py,
written before the hook (rule e65efc68: write the test before the thing, and
especially before a gate).

WHAT THE HOOK IS FOR, measured rather than guessed. The defect class
"dated-artifact-read-as-present-state" has 10 rows since 2026-08-04 and 6 of
them were caught by Joe rather than by the system. Reading all ten back, they
split cleanly in two:

  WAS IT CHOSEN?   rows 8 and 9 — a present state read accurately and a RULING
                   behind it left unread. hooks/drift-claim-gate.py already
                   covers this half: it searches decision-history.md and puts
                   the governing decision in front of the session.

  WAS IT ALREADY   rows 1, 2, 3 and 10 — a claim that something was never run,
  FIXED?           is a live hazard, is blocked, or is still failing, when the
                   fix had already shipped. NOTHING covered this half. The
                   refuting artifact is a COMMIT, and drift-claim-gate cannot
                   find it: it reads only the decision log, and a merged pull
                   request never appears there.

AND IT FIRES AT THE WRONG MOMENT FOR THIS HALF. drift-claim-gate is PreToolUse
on record-defect and add-loop, so it speaks only when a claim is being written
to the record. Eight of the ten rows never reached a write verb at all: the
claim went straight to Joe in chat and the cost was paid there. Row 10 is the
proof — the gate did fire, correctly, on the record-defect call, but by then
the session had already told Joe it would go build a thing that existed. Chat
is the surface, so Stop is the moment, the same argument chat-lint-gate.py
makes for the writing rules.

THE TWO CONDITIONS, both required, for the same anti-alarm-fatigue reason
drift-claim-gate documents: the text must read like a staleness claim AND git
history must actually hold a recent commit about that subject. A staleness
claim with no matching commit is probably a real finding and passes in silence.
Reporting real breakage is core work and must never need an argument.

THE GUARD THAT MATTERS MOST is 'already-cited'. A session that has read the
commit and is quoting it — a correction, a status report, a summary of what
landed — must pass untouched. Without this the gate would block the very
message that fixes the error it exists to catch. Case `cites-the-commit` below
is that message, taken verbatim in shape from the 2026-08-14 correction.

Spawns the REAL hook with REAL Stop payloads, against a REAL throwaway git
repo seeded with known commit subjects. Exit 2 = blocked, 0 = allowed.
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "stale-claim-gate.py")

# The one scrubber (ops/git_env.py). git hands every hook a GIT_DIR pointing
# at the repository that invoked it, and on 2026-08-14 that leaked a fixture
# commit onto live main; the seeded history here and the hook's own reads of
# it must run against the throwaway repo, never whatever GIT_DIR an inherited
# hook invocation exports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

# Commit subjects seeded into the fixture repo. The first is the real one from
# 2026-08-14 that the session read past; the others exist so a claim about an
# unrelated subject has plenty of history to NOT match against.
SEED_COMMITS = [
    "The Friday social batch gets its writer, so building next week's drafts "
    "stops failing the nightly chain (#152)",
    "Register Graph/ as a derived dir in vault-drift-watch (#132)",
    "Prove the offline paper key copy can actually recover a backup (Program 4)",
    "cloud backup: archive the encrypted dump to R2 as the second off-Mac copy",
    "update the system docs and tidy the readme",
]

# (name, assistant_text, expect_block, why)
CASES = [
    # ── the failure this hook was built from ────────────────────────────────
    ("the-2026-08-14-claim",
     "The nightly chain's vault drift watch is still failing on two UNEXPECTED "
     "files, the Friday social batch outputs for next week. It blocks a green "
     "chain every night until it lands. I'll take a worktree, register the "
     "social-batch output path, and push it as a pull request.",
     True,
     "the exact claim from defect row 10, against the commit that refutes it"),

    # ── the guard that keeps the fix from blocking its own correction ───────
    ("cites-the-commit",
     "The fix landed hours before I proposed it: commit 3db2373, 'The Friday "
     "social batch gets its writer, so building next week's drafts stops "
     "failing the nightly chain', merged at 16:26 CDT. The log I quoted was "
     "this morning's run, before that merge.",
     False,
     "a session quoting the commit has plainly read it — never block the "
     "correction itself"),

    ("cites-the-subject-not-the-hash",
     "Already covered: the Friday social batch got its writer in the pull "
     "request that stops the nightly chain failing, so there is nothing left "
     "to build on the vault drift side.",
     False,
     "naming the commit's own subject line counts as having read it"),

    # ── real findings must pass in silence ──────────────────────────────────
    ("no-matching-commit",
     "The renewal radar's pool mapping is still failing every run: the job "
     "role cannot read the leads it suppresses against, and nothing in the "
     "history addresses it. I'll write the grant migration.",
     False,
     "a staleness claim with no commit about it is probably real"),

    ("generic-overlap-only",
     "The system is still broken in the way I described and the docs do not "
     "explain it.",
     False,
     "'system', 'docs' and 'update' are everywhere in any history — a common "
     "word coincidence must never fire the gate (drift-claim-gate learned "
     "this the expensive way on its 2026-08-13 replay)"),

    # ── not a staleness claim at all ────────────────────────────────────────
    ("no-claim-language",
     "The Friday social batch writes next week's drafts into the vault every "
     "Friday at 08:00, and the nightly chain reads them afterwards.",
     False,
     "describing how something works is not asserting it is broken"),

    ("claim-only-inside-a-fence",
     "Here is this morning's log, before the fix landed:\n```\n"
     "FAIL vault drift watch (check, first) (exit 2)\n"
     "the Friday social batch outputs are still failing the nightly chain\n"
     "```\nAll clean on the current code.",
     False,
     "quoted log output is evidence being shown, not a claim being made"),
]


def seed_repo(path):
    env = dict(fixture_env(),
               GIT_AUTHOR_NAME="selftest", GIT_AUTHOR_EMAIL="s@e.test",
               GIT_COMMITTER_NAME="selftest", GIT_COMMITTER_EMAIL="s@e.test")
    def git(*args):
        subprocess.run(["git", "-C", path, *args], check=True, env=env,
                       capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    for i, subject in enumerate(SEED_COMMITS):
        with open(os.path.join(path, f"f{i}.txt"), "w") as fh:
            fh.write(subject)
        git("add", "-A")
        git("commit", "-q", "-m", subject)


def spoken(stdout):
    """The gate's announcement, or "" for silence."""
    try:
        emitted = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return ""
    if "decision" in emitted:            # a block wearing an announce's clothes
        return ""
    return (emitted.get("hookSpecificOutput") or {}).get("additionalContext") or ""


def run_stop(assistant_text, repo, stop_active=False):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                "message": {"content": [{"type": "text",
                                         "text": "what's left?"}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant",
                "message": {"content": [{"type": "text",
                                         "text": assistant_text}]}}) + "\n")
        payload = {"hook_event_name": "Stop", "transcript_path": path,
                   "session_id": "selftest", "stop_hook_active": stop_active}
        # The hook itself shells out to git against CARR_STALE_CLAIM_REPO, so
        # this invocation gets the same scrub as the seeded fixture — an
        # inherited GIT_DIR must not redirect the hook's reads either.
        p = subprocess.run(
            [sys.executable, HOOK], input=json.dumps(payload),
            capture_output=True, text=True, timeout=60,
            env=dict(fixture_env(), CARR_STALE_CLAIM_REPO=repo))
        # DEMOTED 2026-08-23 (the gates-audit council's Stop-gate rationing).
        # This gate exited 2, which reopens the turn and costs a whole extra
        # assistant message; it now announces into context instead. So "caught"
        # is an announcement, and an exit 2 is itself the regression — the same
        # inversion ops/chat-lint-gate-selftest.py made in 2026-08-16 when that
        # gate was demoted for the same reason.
        if p.returncode == 2:
            return False, "REGRESSION: the gate reopened the turn instead of "
        said = spoken(p.stdout or "")
        return bool(said), said + (p.stderr or "")
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1

    passed, bad = 0, []
    with tempfile.TemporaryDirectory() as repo:
        seed_repo(repo)

        for name, text, expect, why in CASES:
            got, _ = run_stop(text, repo)
            ok = got == expect
            passed += ok
            if not ok:
                bad.append(name)
            print(f"  {'ok  ' if ok else 'FAIL'} {name:28} "
                  f"want={'BLOCK' if expect else 'allow'} "
                  f"got={'BLOCK' if got else 'allow'}")
            if not ok:
                print(f"       why this case exists: {why}")

        # Never loops: the reopened turn must pass, same contract every Stop
        # gate in this repo honours.
        got, _ = run_stop(CASES[0][1], repo, stop_active=True)
        passed += (not got)
        if got:
            bad.append("stop-active-loops")
        print(f"  {'ok  ' if not got else 'FAIL'} {'stop-active-never-loops':28} "
              f"want=allow got={'BLOCK' if got else 'allow'}")

        # The block has to be actionable: it names the commit to go read.
        got, out = run_stop(CASES[0][1], repo)
        if got and "social batch gets its writer" in out:
            passed += 1
            print("  ok   the block quotes the commit subject to check")
        else:
            bad.append("block-names-the-commit")
            print(f"  FAIL the block quotes the commit subject — {out[:160]}")

        # THE REGISTER ITSELF, asserted rather than inferred. Every case above
        # reads "caught" through spoken(), so a gate that went back to exit 2
        # would fail them all as silence — but it would fail them with a
        # confusing message. This says the thing directly.
        fd, tpath = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                "message": {"content": [{"type": "text", "text": "what's left?"}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant",
                "message": {"content": [{"type": "text",
                                         "text": CASES[0][1]}]}}) + "\n")
        proc = subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps({"hook_event_name": "Stop", "transcript_path": tpath,
                              "session_id": "selftest", "stop_hook_active": False}),
            capture_output=True, text=True, timeout=60,
            env=dict(os.environ, CARR_STALE_CLAIM_REPO=repo))
        os.unlink(tpath)
        if proc.returncode == 0 and "decision" not in (proc.stdout or ""):
            passed += 1
            print("  ok   it announces without reopening the turn")
        else:
            bad.append("announces-without-reopening")
            print(f"  FAIL it reopened the turn — exit {proc.returncode}: "
                  f"{(proc.stdout or proc.stderr or '')[:120]}")

        # Fails open rather than wedging the session when git is unreachable.
        got, _ = run_stop(CASES[0][1], os.path.join(repo, "does-not-exist"))
        passed += (not got)
        if got:
            bad.append("fails-open-without-git")
        print(f"  {'ok  ' if not got else 'FAIL'} {'fails-open-without-git':28} "
              f"want=allow got={'BLOCK' if got else 'allow'}")

    print(f"\nstale-claim-gate-selftest: {passed}/{passed + len(bad)} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
