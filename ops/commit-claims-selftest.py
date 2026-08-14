#!/usr/bin/env python3
"""
commit-claims-selftest.py — the acceptance test for ops/githooks/commit-msg,
written before the hook itself (rule e65efc68, enforcing rule 24e10ee8).

WHAT THE HOOK IS FOR. On 2026-08-13 a commit message claimed the gate baseline
had been re-blessed, and the diff contained no baseline at all. Whoever read
the message believed the enforcement layer was attested; whoever read the diff
knew it was not. A message is the only part of a commit that nothing checks —
CI runs the tree, the baseline hashes the gates, and the message can say
anything. This hook makes the two named claims that have actually lied agree
with the staged diff:

  1. A message that says the baseline was re-blessed or re-stamped must ship
     ops/config/gate-baseline.json in the same commit.
  2. A body line in the house named-path style — `path/to/file.ext — what
     changed` — must name a path that is actually in the staged diff. That
     convention is how this repo's messages enumerate their own diff, so a
     path-line about an untouched file is a claim the diff contradicts.

WHAT IT MUST NOT DO:

  - It must not block a message that merely DISCUSSES a file mid-prose; only
    the line-leading named-path form is a claim.
  - It must not block a message-only amend: with nothing staged there is no
    diff to disagree with.
  - It must have a working escape hatch (CARR_ALLOW_CLAIM_MISMATCH=1), same
    idiom as its neighbours, because an amend that restages only part of a
    commit legitimately carries claims about files not in the delta.
  - It must not touch the repository that invoked it.

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/commit-claims-selftest.py

ops/ci.sh already globs ops/*-selftest.py, so this is enforced from the commit
that adds it, with no CI change.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "ops" / "githooks" / "commit-msg"
HELPER = REPO / "ops" / "githooks" / "commit-claims-check.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def git(repo, *args, env=None, check_rc=False):
    e = dict(os.environ)
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_COMMON_DIR", "GIT_NAMESPACE"):
        e.pop(var, None)
    if env:
        e.update(env)
    p = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=True, text=True, env=e)
    if check_rc and p.returncode != 0:
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


def make_repo(tmp):
    repo = Path(tmp) / "fixture"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "feature/claims", check_rc=True)
    git(repo, "config", "user.email", "fixture@example.invalid", check_rc=True)
    git(repo, "config", "user.name", "Fixture", check_rc=True)
    hooks = repo / "githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "commit-msg")
    os.chmod(hooks / "commit-msg", 0o755)
    shutil.copy(HELPER, hooks / "commit-claims-check.py")
    git(repo, "config", "core.hooksPath", "githooks", check_rc=True)
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "seed.txt", check_rc=True)
    git(repo, "commit", "-q", "-m", "seed", check_rc=True)
    return repo


def attempt(repo, message, stage: dict | None = None, env=None):
    for name, content in (stage or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(repo, "add", name, check_rc=True)
    return git(repo, "commit", "-m", message, env=env)


print("\nops/githooks/commit-msg — the message and the diff must agree (24e10ee8)")

for required, what in ((HOOK, "hook"), (HELPER, "helper")):
    if not required.exists():
        print(f"  FAIL  the {what} does not exist at {required}")
        print("\n1 check(s) failed: missing file")
        sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    repo = make_repo(tmp)
    head0 = git(repo, "rev-parse", "HEAD").stdout.strip()

    # ── 1. the 2026-08-13 lie is refused ────────────────────────────────────
    r = attempt(repo, "gates: re-blessed the baseline after review",
                stage={"notes.txt": "unrelated\n"})
    check("a re-bless claim without the baseline in the diff is refused",
          r.returncode != 0, f"exit {r.returncode}")
    check("and nothing was committed",
          git(repo, "rev-parse", "HEAD").stdout.strip() == head0)
    msg = (r.stdout + r.stderr).lower()
    check("the refusal names the missing baseline", "gate-baseline.json" in msg)
    check("the refusal names its escape hatch", "carr_allow_claim_mismatch" in msg)
    check("the refused work is still staged",
          "notes.txt" in git(repo, "diff", "--cached", "--name-only").stdout)

    # ── 2. the same claim with the baseline present is allowed ──────────────
    r = attempt(repo, "gates: re-blessed the baseline after review",
                stage={"ops/config/gate-baseline.json": "{}\n"})
    check("a re-bless claim with the baseline staged is allowed",
          r.returncode == 0, r.stderr)

    # ── 3. 're-stamp its baseline hash' is the same claim ───────────────────
    r = attempt(repo, "sync map and re-stamp its baseline hash",
                stage={"other.txt": "x\n"})
    check("a re-stamp claim without the baseline is refused",
          r.returncode != 0, f"exit {r.returncode}")
    git(repo, "reset", "-q", check_rc=True)

    # ── 4. a named-path body line must name a staged path ───────────────────
    r = attempt(repo,
                "improve the widget\n\ntools/widget.py — now twice as fast\n",
                stage={"docs/readme.txt": "hi\n"})
    check("a path-line claiming an untouched file is refused",
          r.returncode != 0, f"exit {r.returncode}")
    check("the refusal names the phantom path",
          "tools/widget.py" in (r.stdout + r.stderr))

    # IT MUST SAY WHY IT READ THAT AS A CLAIM, not merely that it did.
    #
    # 2026-08-14: a commit message narrated two past incidents using the house
    # `path — text` shape for files it did not touch. The gate was right to
    # refuse — that shape MEANS "this commit changes this file" — but the
    # refusal only said the path "is not in the staged diff", which reads as a
    # bookkeeping complaint rather than a misused convention. The author reached
    # for CARR_ALLOW_CLAIM_MISMATCH instead of rewriting the line, which is the
    # weaker of the two doors the gate offers. A gate that does not explain
    # itself gets escaped rather than obeyed.
    out = r.stdout + r.stderr
    check("the refusal quotes the line it objected to",
          "now twice as fast" in out,
          "the author cannot find which line to fix")
    check("the refusal names the convention that made it a claim",
          "—" in out and ("shape" in out.lower() or "convention" in out.lower()),
          "it says WHAT it refused but not WHY it read that as a claim")
    check("the refusal offers rewriting as well as escaping",
          "sentence" in out.lower() or "prose" in out.lower(),
          "only the escape hatch is offered, so the escape is what gets used")
    git(repo, "reset", "-q", check_rc=True)

    r = attempt(repo,
                "improve the widget\n\ntools/widget.py — now twice as fast\n",
                stage={"tools/widget.py": "print('fast')\n"})
    check("a path-line matching the staged diff is allowed",
          r.returncode == 0, r.stderr)

    # ── 5. prose that merely mentions a file is not a claim ─────────────────
    r = attempt(repo,
                "align with ops/ci.sh behaviour\n\n"
                "The check mirrors what ops/ci.sh already does for tests, "
                "see hooks/gate-integrity.py for the baseline idea.\n",
                stage={"aligned.txt": "ok\n"})
    check("mid-prose file mentions are not claims", r.returncode == 0, r.stderr)

    # ── 5b. body prose about a past re-bless is not a claim ─────────────────
    r = attempt(repo,
                "harden the widget check\n\n"
                "On 2026-08-13 a commit claimed the baseline was re-blessed\n"
                "and lied; this check exists because of that incident.\n",
                stage={"widget-notes.txt": "context\n"})
    check("body prose about a re-bless incident is not a claim",
          r.returncode == 0, r.stderr)

    # ── 6. an ordinary message is untouched ─────────────────────────────────
    r = attempt(repo, "tidy the seed", stage={"seed.txt": "seed v2\n"})
    check("a message with no claims is allowed", r.returncode == 0, r.stderr)

    # ── 7. a message-only amend has no diff to disagree with ────────────────
    r = git(repo, "commit", "--amend", "-m",
            "tidy the seed\n\ntools/widget.py — untouched here\n")
    check("a message-only amend is not refused", r.returncode == 0,
          "with nothing staged there is no diff to contradict")

    # ── 8. the escape hatch works ───────────────────────────────────────────
    r = attempt(repo, "gates: re-blessed the baseline after review",
                stage={"hatch.txt": "x\n"},
                env={"CARR_ALLOW_CLAIM_MISMATCH": "1"})
    check("CARR_ALLOW_CLAIM_MISMATCH=1 lets a mismatched claim through",
          r.returncode == 0, r.stderr)

    # ── 9. the hook did not touch the repository that invoked it ────────────
    outer_status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                                  capture_output=True, text=True).stdout
    check("the outer repository gained no fixture files",
          "widget.py" not in outer_status and "notes.txt" not in outer_status)

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("COMMIT CLAIMS SELFTEST PASSED: a message's named claims must appear in "
      "the staged diff, prose stays free, and the escape hatch works.")
